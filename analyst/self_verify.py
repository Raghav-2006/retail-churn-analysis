"""Measure the self-verification step (Phase 8, prompt v5 = v4 + answer verification).

    python -m analyst.self_verify        # writes metrics/analyst_v5_31_flash_lite{,_defs}.json and metrics/self_verify.json

1. Replay. v5 differs from v4 only AFTER the answer is written, and every v4 generation is
   deterministic and recorded, so v5 is measured by running the verification step on each v4 answer
   (same SQL, same result, same prose). Only answers it flags cost a Gemini call (the rewrite).
   Grades: an answer the step abstains on becomes "abstained" (answerable) or "refused" (unanswerable).
2. Fault injection. Real v4 answers turned out to contain no unsupported numbers, so the step's
   detection and repair are measured on corrupted copies: in each distinct answer, the first digit of
   the first number that restates a result cell (the headline figure) is changed (2,145 -> 3,145;
   38.70% -> 48.70%). Detection = share flagged; repair = share the rewrite fixes; the rest abstain.
"""
import json
import time

from analyst import budget
from analyst.evaluate import eval_set_sha, metrics_name, summarise
from analyst.judge import MODEL, judgeable
from analyst.verify import _matches, numbers, unsupported_numbers
from src.metrics import METRICS, save_metrics


def _load(version: str, eval_set: str) -> list[dict]:
    return json.loads((METRICS / f"{metrics_name(version, MODEL, eval_set)}_records.json").read_text())


def corrupt(answer: str, rows: list[list]) -> tuple[str, str] | None:
    """(corrupted answer, original number) or None when no number in the answer restates a result cell."""
    cells = [float(v) for r in rows for v in r if isinstance(v, (int, float)) and not isinstance(v, bool)]
    for value, decimals, raw in numbers(answer):
        if any(_matches(value, decimals, c) for c in cells) and not (1900 <= value <= 2100 and value.is_integer()):
            i = next(k for k, ch in enumerate(raw) if ch.isdigit())
            new = raw[:i] + str(int(raw[i]) % 9 + 1) + raw[i + 1:]
            return answer.replace(raw, new, 1), raw
    return None


def replay(agent: object) -> dict:
    out = {}
    for eval_set in ("frozen", "defs"):
        recs, v5 = _load("v4", eval_set), []
        for r in recs:
            r5 = dict(r, verification=None, unsupported=[])
            if judgeable(r):
                t0 = time.perf_counter()
                status, answer, bad = agent.verify_answer(r["question"], r["sql"], r["columns"], r["result"], r["answer"])
                r5.update(verification=status, answer=answer, unsupported=bad,
                          latency_s=round(r["latency_s"] + time.perf_counter() - t0, 2))
                if status == "abstained":
                    r5["outcome"] = "refused" if r["difficulty"] == "unanswerable" else "abstained"
            v5.append(r5)
        s4, s5 = summarise(recs), summarise(v5)
        name = metrics_name("v5", MODEL, eval_set)
        save_metrics(name, {"prompt_version": "v5", "model": MODEL, "eval_set": eval_set, "replayed_from": "v4",
                            "eval_set_sha256": eval_set_sha(eval_set), "runs": 2, **s5,
                            "verification": {k: sum(x.get("verification") == k for x in v5)
                                             for k in ("pass", "corrected", "abstained")}})
        (METRICS / f"{name}_records.json").write_text(json.dumps(v5, indent=1, default=str) + "\n")
        out[eval_set] = {"v4_confidently_wrong_pct": s4["confidently_wrong_pct"],
                         "v5_confidently_wrong_pct": s5["confidently_wrong_pct"],
                         "v4_execution_accuracy_pct": s4["execution_accuracy_pct"],
                         "v5_execution_accuracy_pct": s5["execution_accuracy_pct"],
                         "answers_checked": sum(judgeable(r) for r in recs),
                         "verification": {k: sum(x.get("verification") == k for x in v5)
                                          for k in ("pass", "corrected", "abstained")}}
    return out


def injection(agent: object) -> dict:
    recs = [r for s in ("frozen", "defs") for r in _load("v4", s) if judgeable(r)]
    seen, cases, skipped = set(), [], 0
    for r in recs:
        if (r["id"], r["answer"]) in seen:
            continue
        seen.add((r["id"], r["answer"]))
        c = corrupt(r["answer"], r["result"])
        if c is None:
            skipped += 1
            continue
        cases.append((r, *c))
    detected, results = 0, []
    for r, bad_answer, original in cases:
        flagged = unsupported_numbers(bad_answer, r["question"], r["columns"], r["result"], r["sql"])
        detected += bool(flagged)
        status, final, _ = agent.verify_answer(r["question"], r["sql"], r["columns"], r["result"], bad_answer)
        results.append({"id": r["id"], "original": original, "corrupted_answer": bad_answer, "flagged": flagged,
                        "status": status, "final_answer": final,
                        "original_number_restored": status == "corrected" and original in final})
    n = len(cases)
    pct = lambda k: round(100 * k / n, 1) if n else None
    return {
        "distinct_answers": len(seen), "corrupted": n, "skipped_no_headline_number": skipped,
        "detected": detected, "detection_pct": pct(detected),
        "corrected": sum(x["status"] == "corrected" for x in results),
        "corrected_pct": pct(sum(x["status"] == "corrected" for x in results)),
        "original_number_restored": sum(x["original_number_restored"] for x in results),
        "abstained": sum(x["status"] == "abstained" for x in results),
        "abstained_pct": pct(sum(x["status"] == "abstained" for x in results)),
        "missed": sum(x["status"] == "pass" for x in results),
        "cases": results,
    }


def main() -> dict:
    from analyst.agent import Analyst

    agent = Analyst("v5", model=MODEL, use_cache=False)
    recs = [r for s in ("frozen", "defs") for r in _load("v4", s) if judgeable(r)]
    distinct = len({(r["id"], r["answer"]) for r in recs})
    # worst case: every real answer and every corrupted answer needs one rewrite
    budget.estimate("self-verification replay + fault injection", len(recs) + distinct, MODEL, 1_200, 150)
    calls0 = budget.load()["calls"]
    try:
        res = {"replay": replay(agent), "injection": injection(agent), "complete": True}
    except budget.BudgetExceeded as err:
        res = {"complete": False, "stopped_reason": str(err)}
    res["gemini_calls"] = budget.load()["calls"] - calls0
    save_metrics("self_verify", res)
    print(json.dumps({k: v for k, v in res.items() if k != "injection"}, indent=1))
    if "injection" in res:
        print(json.dumps({k: v for k, v in res["injection"].items() if k != "cases"}, indent=1))
    return res


if __name__ == "__main__":
    main()
