"""LLM-as-judge (Phase 8): is each prose answer faithful to its SQL result, and are its citations right?

    python -m analyst.judge --version v4          # judge every answered v4 eval record, 2 runs x 65 questions

Same model as the agent (gemini-3.1-flash-lite, the only one in the budget), temperature 0, JSON out.
The judge sees the question, the SQL, the result the SQL returned, the prose answer, the doc ids the
agent cited and the full text of every doc it was shown. It does NOT see the reference SQL or the
grade: faithfulness is "supported by the result", not "correct", so the two measure different
failures (a faithful answer about a wrong query is still wrong).

Verdicts are cached in data/judge_cache.jsonl, keyed by the exact inputs, so a re-run costs nothing.
Calibration against the hand labels: python -m analyst.agreement.
"""
import argparse
import hashlib
import json
from pathlib import Path

from analyst import budget
from rag.docs import load_docs
from src.metrics import METRICS, save_metrics

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "judge_cache.jsonl"
MODEL = "gemini-3.1-flash-lite"
JUDGE_VERSION = "j1"

JUDGE_SYSTEM = """You are a strict evaluator of answers written by an AI data analyst. You check two things.

FAITHFULNESS: is every number and factual claim in the ANSWER supported by the SQL RESULT?
- Supported means the number appears in the result, or is a direct rounding or reformatting of one
  (445.6018 -> "£445.60"; 0.3870 -> "38.7%"; 17068582.72 -> "£17.1 million"), or is a simple count of
  the result rows.
- Unsupported: a number that is not in the result, a wrong rounding, a sum/average/ranking the result
  does not contain, a claim about a different period, unit or entity than the result shows, or an
  invented explanation presented as fact.
- A short description of the business definition used (e.g. "active = ordered in the last 90 days") is
  fine if the cited docs say so. Judge only whether the answer matches the result; do NOT judge
  whether the SQL was the right query.

CITATION CORRECTNESS: are the CITED DOCS the right ones?
- Correct: every cited doc is current (not deprecated), is relevant, and what it defines matches what
  the SQL actually computes; and if the question depends on a business definition that one of the
  SHOWN DOCS provides, that doc is cited. No citations is correct only when the question needs no
  business definition or caveat beyond plain column arithmetic.
- Incorrect: citing a deprecated doc, citing a doc whose definition the SQL does not follow, citing
  irrelevant docs as the basis for the answer, or omitting the doc whose definition was needed.

Respond with JSON only:
{"faithful": true|false, "faithfulness_reason": "<one sentence>",
 "citation_correct": true|false, "citation_reason": "<one sentence>"}"""


def _docs_text(shown: list[str]) -> str:
    docs = {d.id: d for d in load_docs()}
    blocks = []
    for doc_id in shown:
        d = docs.get(doc_id)
        if d:
            status = f"DEPRECATED, superseded by {d.superseded_by}" if d.deprecated else "current"
            blocks.append(f"[doc: {d.id} | {d.doc_type} | updated {d.last_updated} | {status}]\n{d.title}\n{d.body}")
    return "\n\n".join(blocks)


def judge_input(rec: dict) -> str:
    shown = list(dict.fromkeys(c.split("#")[0] for c in rec.get("retrieved") or []))
    result = json.dumps({"columns": rec["columns"], "rows": rec["result"], "row_count": rec["rows"]}, default=str)
    return (f"QUESTION: {rec['question']}\n\nSQL:\n{rec['sql']}\n\nSQL RESULT (first rows, JSON):\n{result}\n\n"
            f"ANSWER:\n{rec['answer']}\n\nCITED DOCS: {json.dumps(rec.get('citations') or [])}\n\n"
            f"SHOWN DOCS (full text):\n{_docs_text(shown) or '(none)'}")


def _key(contents: str) -> str:
    return hashlib.sha256(f"{MODEL}|{JUDGE_VERSION}|{contents}".encode()).hexdigest()[:20]


def _load_cache() -> dict:
    if not CACHE.exists():
        return {}
    return {e["key"]: e for e in (json.loads(x) for x in CACHE.read_text().splitlines() if x.strip())}


class Judge:
    def __init__(self) -> None:
        from analyst.agent import Analyst

        # reuse the agent's Gemini plumbing (backoff, spend guard); the judge has its own system prompt
        self._llm = Analyst("v4", model=MODEL, use_cache=False)
        self.cache = _load_cache()
        self.calls = 0

    def judge(self, rec: dict, tag: str = "judge") -> dict:
        contents = judge_input(rec)
        key = _key(contents)
        if key in self.cache:
            return self.cache[key]["verdict"]
        raw = self._llm._generate(contents, purpose=tag, system=JUDGE_SYSTEM)
        self.calls += 1
        out = self._llm._parse(raw)
        verdict = {"faithful": bool(out.get("faithful")), "faithfulness_reason": out.get("faithfulness_reason", ""),
                   "citation_correct": bool(out.get("citation_correct")),
                   "citation_reason": out.get("citation_reason", ""), "parsed": "faithful" in out}
        self.cache[key] = {"key": key, "verdict": verdict}
        CACHE.parent.mkdir(exist_ok=True)
        with CACHE.open("a") as f:
            f.write(json.dumps(self.cache[key]) + "\n")
        return verdict


def judgeable(rec: dict) -> bool:
    """Only answers that state a result: abstentions and failed queries have nothing to be faithful to."""
    return rec["outcome"] in {"correct", "wrong", "answered_unanswerable"} and bool(rec.get("answer"))


def run(version: str = "v4", records_names: tuple[str, ...] = ("frozen", "defs")) -> dict:
    from analyst.evaluate import metrics_name

    recs = []
    for s in records_names:
        for r in json.loads((METRICS / f"{metrics_name(version, MODEL, s)}_records.json").read_text()):
            recs.append(dict(r, set=s))
    todo = [r for r in recs if judgeable(r)]
    budget.estimate(f"judge on {len(todo)} {version} answers", len(todo), MODEL, 3_000, 250)
    j, out, stopped = Judge(), [], None
    try:
        for r in todo:
            v = j.judge(r, tag="judge")
            out.append({"id": r["id"], "run": r["run"], "set": r["set"], "outcome": r["outcome"], **v})
            print(f"[judge {version}] {r['id']} run{r['run']} faithful={v['faithful']!s:<5} "
                  f"citation={v['citation_correct']!s:<5} calls {j.calls} spent CA${budget.spent_cad():.4f}", flush=True)
    except budget.BudgetExceeded as err:
        stopped = str(err)
        print(f"STOPPED: {err}", flush=True)
    n = len(out)
    summary = {
        "version": version, "judge_model": MODEL, "judge_version": JUDGE_VERSION, "judged": n,
        "complete": stopped is None, "stopped_reason": stopped, "gemini_calls": j.calls,
        "faithful_pct": round(100 * sum(o["faithful"] for o in out) / n, 1) if n else None,
        "citation_correct_pct": round(100 * sum(o["citation_correct"] for o in out) / n, 1) if n else None,
        "unparsed": sum(not o["parsed"] for o in out),
        "faithful_pct_by_outcome": {o: round(100 * sum(x["faithful"] for x in out if x["outcome"] == o)
                                             / max(1, sum(x["outcome"] == o for x in out)), 1)
                                    for o in sorted({x["outcome"] for x in out})},
        "citation_correct_pct_by_set": {s: round(100 * sum(x["citation_correct"] for x in out if x["set"] == s)
                                                 / max(1, sum(x["set"] == s for x in out)), 1)
                                        for s in sorted({x["set"] for x in out})},
    }
    save_metrics(f"judge_{version}", {**summary, "verdicts": out})
    return summary


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--version", default="v4")
    args = p.parse_args()
    print(json.dumps(run(args.version), indent=1))


if __name__ == "__main__":
    main()
