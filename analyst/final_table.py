"""The Phase 8 summary table: one row per prompt version for gemini-3.1-flash-lite.

    python -m analyst.final_table      # writes metrics/analyst_final.json

v1 baseline -> v2 +glossary -> v3 +SQL self-check -> v4 +RAG -> v5 +answer self-verification.
Two blocks, because the question sets differ:
  - frozen 55 (eval set v2): v1-v3 from Part 3 (3 runs each), v4 and v5 (2 runs);
  - all 65 (frozen 55 + 10 definition questions, 2 runs): v3, v4, v5.
Cost per question = the SQL-writing, self-check and verification calls, from the ledger; the prose
answer and the judge are excluded so versions are comparable. v1 and v2 ran before the ledger existed,
so their cost is not measured.
"""
import json

import numpy as np

from analyst import budget
from analyst.evaluate import metrics_name, summarise
from src.metrics import METRICS, save_metrics

MODEL = "gemini-3.1-flash-lite"
LABELS = {"v1": "baseline", "v2": "+ glossary", "v3": "+ SQL self-check", "v4": "+ RAG (knowledge base)",
          "v5": "+ answer self-verification"}


def _records(version: str, eval_set: str, runs: set[int] | None = None) -> list[dict]:
    recs = json.loads((METRICS / f"{metrics_name(version, MODEL, eval_set)}_records.json").read_text())
    return [dict(r, set=eval_set) for r in recs if runs is None or r["run"] in runs]


def _cost(version: str, question_runs: int) -> float | None:
    by = budget.load()["by_purpose"]
    cad = sum(by.get(f"{version}:{p}", {}).get("est_cad", 0.0) for p in ("agent", "self_check", "verify"))
    return round(cad / question_runs, 5) if cad and question_runs else None


def row(version: str, recs: list[dict], block: str, cost: float | None, note: str = "") -> dict:
    s = summarise(recs)
    lat = np.array([r["latency_s"] - r.get("narrate_s", 0.0) for r in recs])
    cw = s["outcomes"].get("wrong", 0) + s["outcomes"].get("answered_unanswerable", 0)
    return {"block": block, "version": version, "label": LABELS[version], "runs": len({r["run"] for r in recs}),
            "question_runs": len(recs), "execution_accuracy_pct": s["execution_accuracy_pct"],
            "abstention_accuracy_pct": s["abstention_accuracy_pct"],
            "confidently_wrong_pct": s["confidently_wrong_pct"], "confidently_wrong": f"{cw}/{len(recs)}",
            "latency_p50_s": round(float(np.percentile(lat, 50)), 2),
            "latency_p95_s": round(float(np.percentile(lat, 95)), 2),
            "cost_per_question_cad": cost, "note": note}


def main() -> dict:
    rows = []
    v3_defs = _records("v3", "defs")
    v3_cost = _cost("v3", len(v3_defs))
    v4_all = [*_records("v4", "frozen"), *_records("v4", "defs")]
    v4_cost = _cost("v4", len(v4_all))
    v5_all = [*_records("v5", "frozen"), *_records("v5", "defs")]
    # the real v4 answers needed no rewrite (0 verify calls); the ledger's v5:verify calls are all the
    # fault-injection test, so they are not a per-question cost
    rewrites = sum(r.get("verification") in ("corrected", "abstained") for r in v5_all)
    v5_cost = v4_cost if rewrites == 0 else round(v4_cost + (_cost("v5", len(v5_all)) or 0), 5)
    for v in ("v1", "v2", "v3"):
        rows.append(row(v, _records(v, "frozen"), "frozen55", v3_cost if v == "v3" else None,
                        "3 runs (Part 3)" + ("; cost measured on its 20 definition-question runs" if v == "v3" else
                                             "; cost not measured (pre-ledger)")))
    rows.append(row("v4", _records("v4", "frozen"), "frozen55", v4_cost))
    rows.append(row("v5", _records("v5", "frozen"), "frozen55", v5_cost,
                    f"v4 answers replayed through verification; {rewrites} rewrites needed"))
    rows.append(row("v3", [*_records("v3", "frozen", {0, 1}), *v3_defs], "all65", v3_cost,
                    "frozen 55 = cached runs 0-1"))
    rows.append(row("v4", v4_all, "all65", v4_cost))
    rows.append(row("v5", v5_all, "all65", v5_cost,
                    f"v4 answers replayed through verification; {rewrites} rewrites needed"))
    out = {"model": MODEL, "rows": rows}
    save_metrics("analyst_final", out)
    for r in rows:
        print(f"{r['block']:<9}{r['version']} {r['label']:<28} exec {r['execution_accuracy_pct']:>6} abst "
              f"{r['abstention_accuracy_pct']:>6} CW {r['confidently_wrong_pct']:>5} ({r['confidently_wrong']:>6}) "
              f"p50 {r['latency_p50_s']} p95 {r['latency_p95_s']} cost {r['cost_per_question_cad']}")
    return out


if __name__ == "__main__":
    main()
