"""Evaluate the analyst against the ground-truth question set.

    python -m analyst.evaluate --prompt v1 --runs 3
    python -m analyst.evaluate --compare v1 v2      # writes metrics/analyst.json for RESULTS.md

Each question gets one outcome per run:
  correct        answerable, answered, result set matches the reference
  wrong          answerable, answered, result set does NOT match     -> confidently wrong
  answered_unanswerable  unanswerable, but it returned a result      -> confidently wrong
  abstained      answerable, but it refused (safe, but unhelpful)
  refused        unanswerable and it refused (correct)
  error          the SQL still failed after the retry (visible failure, not confident)

Headline metric: confidently-wrong rate = (wrong + answered_unanswerable) / all questions.
Those are the dangerous cases: a number that looks authoritative and is not.

Execution accuracy compares RESULTS, not SQL text: two different queries that
return the same numbers are both right, and a query that looks right but
returns the wrong numbers is wrong. Matching is order-insensitive, allows
extra columns in the agent's result, and uses a small numeric tolerance.
"""
import argparse
import itertools
import json
import math
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import numpy as np
import yaml

from analyst.agent import Analyst, run_readonly
from src.metrics import METRICS, save_metrics

EVAL_SET = Path(__file__).parent / "eval_set.yaml"
DIFFICULTIES = ["easy", "medium", "hard", "unanswerable"]
CONFIDENTLY_WRONG = {"wrong", "answered_unanswerable"}


def slug(model: str) -> str:
    return model.replace("gemini-", "").replace(".", "").replace("-", "_")


def load_eval_set() -> list[dict]:
    return yaml.safe_load(EVAL_SET.read_text())


# ---- result-set matching ------------------------------------------------------------
def norm(v):
    """Normalise one cell so equivalent values from different queries compare equal."""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float, Decimal)):
        return float(v)
    if isinstance(v, datetime):
        return v.date().isoformat() if v.time() == datetime.min.time() else v.isoformat(sep=" ")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, str):
        s = v.strip()
        # ISO timestamps that came back through JSON
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            try:
                return norm(datetime.fromisoformat(s))
            except ValueError:
                pass
        try:
            return float(s)
        except ValueError:
            return s
    return str(v)


def close(a, b) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return math.isclose(a, b, rel_tol=1e-3, abs_tol=0.01)
    return a == b


def _sort_key(v):
    return (0, v, "") if isinstance(v, float) else (1, 0.0, "" if v is None else str(v))


def columns_match(ref_col: list, pred_col: list) -> bool:
    return all(close(a, b) for a, b in zip(sorted(ref_col, key=_sort_key), sorted(pred_col, key=_sort_key)))


MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
               "october", "november", "december"]


def month_variants(col: list) -> list[list]:
    """Other ways to write a month-level date column, all equally correct answers:
    '2011-03', 'March 2011', 'Mar 2011', and (when every row is in one year) 3.0 or 'March'."""
    if not col or not all(isinstance(v, str) and len(v) == 10 and v.endswith("-01") for v in col):
        return []
    years = {v[:4] for v in col}
    ym = [v[:7] for v in col]
    names = [MONTH_NAMES[int(v[5:7]) - 1] for v in col]
    variants = [ym,
                [f"{n.capitalize()} {v[:4]}" for n, v in zip(names, col)],
                [f"{n[:3].capitalize()} {v[:4]}" for n, v in zip(names, col)]]
    if len(years) == 1:
        variants += [[float(v[5:7]) for v in col], [n.capitalize() for n in names]]
    return variants


def _canon(v):
    return v.lower().strip() if isinstance(v, str) else v


def results_match(ref_rows: list, pred_rows: list, percent: bool = False) -> bool:
    """True if the prediction contains the reference result (same rows, possibly extra columns).

    Month-level date keys may be written in any of the equivalent forms in month_variants().
    With percent=True (questions asking for a share or % change), numeric columns may also be
    given as a fraction: 25.0 and 0.25 are both accepted.
    """
    ref = [[norm(v) for v in r] for r in ref_rows]
    pred = [[_canon(norm(v)) for v in r] for r in pred_rows]
    if len(ref) != len(pred):
        return False
    if not ref:
        return True
    n_ref, n_pred = len(ref[0]), len(pred[0])
    if n_pred < n_ref:
        return False
    ref_cols = [[r[j] for r in ref] for j in range(n_ref)]
    pred_cols = [[r[k] for r in pred] for k in range(n_pred)]
    # every acceptable spelling of each reference column
    def fractions(col):
        return [[v / 100 for v in col]] if percent and all(isinstance(v, float) for v in col) else []

    variants = [[[_canon(v) for v in c] for c in [col, *month_variants(col), *fractions(col)]] for col in ref_cols]
    # (variant, agent column) pairs whose values match as a multiset
    candidates = [[(vi, k) for vi, var in enumerate(variants[j]) for k in range(n_pred)
                   if columns_match(var, pred_cols[k])] for j in range(n_ref)]
    if any(not c for c in candidates):
        return False
    for combo in itertools.islice(itertools.product(*candidates), 2000):
        ks = [k for _, k in combo]
        if len(set(ks)) < len(ks):
            continue
        target = [[variants[j][vi][i] for j, (vi, _) in enumerate(combo)] for i in range(len(ref))]
        projected = [[r[k] for k in ks] for r in pred]
        key = lambda row: [_sort_key(v) for v in row]  # noqa: E731
        if all(all(close(a, b) for a, b in zip(x, y))
               for x, y in zip(sorted(target, key=key), sorted(projected, key=key))):
            return True
    return False


# ---- grading --------------------------------------------------------------------------
def grade(item: dict, ans, reference: list | None) -> str:
    answerable = item["difficulty"] != "unanswerable"
    if ans.abstained:
        return "abstained" if answerable else "refused"
    if ans.error is not None:
        return "error"
    if not answerable:
        return "answered_unanswerable"
    return "correct" if results_match(reference, ans.rows, percent=item.get("unit") == "percent") else "wrong"


def summarise(records: list[dict]) -> dict:
    n = len(records)
    answerable = [r for r in records if r["difficulty"] != "unanswerable"]
    unanswerable = [r for r in records if r["difficulty"] == "unanswerable"]
    answered = [r for r in records if r["outcome"] in {"correct", "wrong", "answered_unanswerable"}]
    outcomes = Counter(r["outcome"] for r in records)
    cw = sum(r["outcome"] in CONFIDENTLY_WRONG for r in records)
    return {
        "questions": n,
        "execution_accuracy_pct": round(100 * sum(r["outcome"] == "correct" for r in answerable) / len(answerable), 2),
        "abstention_accuracy_pct": round(100 * sum(r["outcome"] == "refused" for r in unanswerable) / len(unanswerable), 2),
        "false_abstention_pct": round(100 * sum(r["outcome"] == "abstained" for r in answerable) / len(answerable), 2),
        "overall_accuracy_pct": round(100 * sum(r["outcome"] in {"correct", "refused"} for r in records) / n, 2),
        "confidently_wrong_pct": round(100 * cw / n, 2),
        "confidently_wrong_of_answered_pct": round(100 * cw / len(answered), 2) if answered else 0.0,
        "outcomes": dict(outcomes),
        "by_difficulty": {
            d: round(100 * sum(r["outcome"] in {"correct", "refused"} for r in records if r["difficulty"] == d)
                     / max(1, sum(r["difficulty"] == d for r in records)), 2)
            for d in DIFFICULTIES
        },
        "retried": sum(r["attempts"] > 1 for r in records),
        "self_check": dict(Counter(r.get("self_check") for r in records if r.get("self_check"))),
    }


def evaluate(prompt_version: str, runs: int = 3, model: str | None = None) -> dict:
    items = load_eval_set()
    references = {
        it["id"]: run_readonly(it["sql"], max_rows=10_000)[1] for it in items if "sql" in it
    }
    kwargs = {"model": model} if model else {}
    records = []
    for run in range(runs):
        agent = Analyst(prompt_version, narrate=False, cache_tag=f"eval-run{run}", **kwargs)
        for it in items:
            ans = agent.ask(it["question"])
            outcome = grade(it, ans, references.get(it["id"]))
            records.append({
                "run": run, "id": it["id"], "difficulty": it["difficulty"], "question": it["question"],
                "outcome": outcome, "sql": ans.sql, "error": ans.error, "attempts": ans.attempts,
                "latency_s": ans.latency_s, "backoff_s": ans.backoff_s, "rows": len(ans.rows),
                "self_check": ans.self_check,
            })
            print(f"[{prompt_version} run {run}] {it['id']:<4} {outcome:<22} {ans.latency_s:>6.1f}s", flush=True)

    per_run = [summarise([r for r in records if r["run"] == k]) for k in range(runs)]
    pooled = summarise(records)
    lat = np.array([r["latency_s"] for r in records])
    keys = ["execution_accuracy_pct", "abstention_accuracy_pct", "confidently_wrong_pct", "overall_accuracy_pct"]
    result = {
        "prompt_version": prompt_version,
        "model": agent.model,
        "runs": runs,
        **pooled,
        "per_run": {k: [p[k] for p in per_run] for k in keys},
        "latency_p50_s": round(float(np.percentile(lat, 50)), 2),
        "latency_p95_s": round(float(np.percentile(lat, 95)), 2),
        "failures": failure_table(records),
    }
    name = f"analyst_{prompt_version}_{slug(agent.model)}"
    save_metrics(name, result)
    (METRICS / f"{name}_records.json").write_text(json.dumps(records, indent=1, default=str) + "\n")
    return result


def failure_table(records: list[dict]) -> list[dict]:
    """Questions that were not right in every run, with how often each outcome happened."""
    by_q: dict[str, list[dict]] = {}
    for r in records:
        by_q.setdefault(r["id"], []).append(r)
    rows = []
    for qid, rs in by_q.items():
        c = Counter(r["outcome"] for r in rs)
        if set(c) <= {"correct", "refused"}:
            continue
        example = next((r for r in rs if r["outcome"] not in {"correct", "refused"}), rs[0])
        rows.append({"id": qid, "difficulty": rs[0]["difficulty"], "question": rs[0]["question"],
                     "outcomes": dict(c), "example_sql": example["sql"], "example_error": example["error"]})
    return rows


def compare(before: str, after: str, models: list[str]) -> None:
    """Collect before/after metrics for each model into metrics/analyst.json (read by make_results.py)."""
    from src.metrics import load_metrics

    rows = []
    for model in models:
        b = load_metrics(f"analyst_{before}_{slug(model)}")
        a = load_metrics(f"analyst_{after}_{slug(model)}")
        rows.append({"model": model, "before": b, "after": a})
    items = load_eval_set()
    save_metrics("analyst", {"before_version": before, "after_version": after, "models": rows,
                             "eval_questions": len(items),
                             "eval_mix": dict(Counter(it["difficulty"] for it in items))})


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--prompt", default="v1")
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--model")
    p.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"))
    p.add_argument("--models", nargs="+", default=["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"])
    args = p.parse_args()
    if args.compare:
        compare(*args.compare, args.models)
        return
    res = evaluate(args.prompt, args.runs, args.model)
    print(json.dumps({k: v for k, v in res.items() if k != "failures"}, indent=1))
    for f in res["failures"]:
        print(f"\n{f['id']} [{f['difficulty']}] {f['question']}\n  {f['outcomes']}\n  SQL: {f['example_sql']}\n"
              f"  error: {f['example_error']}")


if __name__ == "__main__":
    main()
