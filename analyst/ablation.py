"""Phase 7 ablation: the analyst without RAG (v3) vs with RAG (v4), gemini-3.1-flash-lite, 2 runs each.

    python -m analyst.ablation        # writes metrics/rag_ablation.json

No-RAG baseline on the frozen 55: the cached v3 records (runs 0 and 1 of the 3 that were run in
Part 3). This is valid because the question file (SHA-256 checked below), the prompt and the model are
identical; v4 differs from v3 only by the retrieved knowledge and the citation field. On the 10
definition questions both conditions were run fresh, 2 runs each.
"""
import json
from collections import Counter

import numpy as np

from analyst import budget
from analyst.evaluate import eval_set_sha, metrics_name, summarise
from src.metrics import METRICS, load_metrics, save_metrics

MODEL = "gemini-3.1-flash-lite"
RUNS = (0, 1)
KEYS = ["execution_accuracy_pct", "abstention_accuracy_pct", "false_abstention_pct", "confidently_wrong_pct",
        "confidently_wrong_of_answered_pct", "overall_accuracy_pct"]


def records(version: str, eval_set: str) -> list[dict]:
    name = metrics_name(version, MODEL, eval_set)
    meta = load_metrics(name)
    if meta["eval_set_sha256"] != eval_set_sha(eval_set):
        raise SystemExit(f"{name} was graded on a different {eval_set} question file")
    recs = json.loads((METRICS / f"{name}_records.json").read_text())
    return [dict(r, set=eval_set) for r in recs if r["run"] in RUNS]


def latency(recs: list[dict]) -> dict:
    lat = np.array([r["latency_s"] - r.get("narrate_s", 0.0) for r in recs])
    return {"latency_p50_s": round(float(np.percentile(lat, 50)), 2),
            "latency_p95_s": round(float(np.percentile(lat, 95)), 2)}


def cost_per_question(version: str, question_runs: int) -> float | None:
    """Estimated CA$ per question from the ledger: SQL (incl. retries) + self-check calls; narration and judge excluded."""
    by = budget.load()["by_purpose"]
    cad = sum(by.get(f"{version}:{purpose}", {}).get("est_cad", 0.0) for purpose in ("agent", "self_check"))
    return round(cad / question_runs, 5) if question_runs else None


def citation_stats(recs: list[dict]) -> dict:
    """v4 only: did the answer cite the doc whose definition it needed, and did it cite a trap doc?"""
    import yaml

    from rag.evaluate import RELEVANCE

    rel = yaml.safe_load(RELEVANCE.read_text())
    deprecated = {"metric-churn-2010", "metric-active-customer-2009", "metric-aov-legacy"}
    defs = [r for r in recs if r["set"] == "defs" and r["outcome"] != "abstained"]
    answered = [r for r in recs if r["outcome"] in {"correct", "wrong"}]
    return {
        "answered_with_citation_pct": round(100 * sum(bool(r["citations"]) for r in answered) / len(answered), 1),
        "defs_cite_gold_doc_pct": round(100 * sum(rel[r["id"]]["relevant"][0] in r["citations"] for r in defs)
                                        / max(1, len(defs)), 1),
        "cites_deprecated_doc": sum(bool(set(r["citations"]) & deprecated) for r in recs),
        "cites_unretrieved_doc": sum(bool({c for c in r["citations"]} - {x.split("#")[0] for x in r["retrieved"]})
                                     for r in recs),
        "cited_docs": dict(Counter(c for r in recs for c in r["citations"]).most_common(12)),
    }


def main() -> dict:
    out = {"model": MODEL, "runs": list(RUNS), "conditions": {}}
    by_cond = {"no_rag_v3": [*records("v3", "frozen"), *records("v3", "defs")],
               "rag_v4": [*records("v4", "frozen"), *records("v4", "defs")]}
    for cond, recs in by_cond.items():
        version = cond.split("_")[-1]
        row = {}
        for subset, sub in (("frozen55", [r for r in recs if r["set"] == "frozen"]),
                            ("defs10", [r for r in recs if r["set"] == "defs"]),
                            ("all65", recs)):
            s = summarise(sub)
            row[subset] = {**{k: s[k] for k in KEYS}, "question_runs": len(sub), "outcomes": s["outcomes"],
                           "per_run_execution_accuracy_pct": [summarise([r for r in sub if r["run"] == k])
                                                              ["execution_accuracy_pct"] for k in RUNS],
                           "per_run_confidently_wrong_pct": [summarise([r for r in sub if r["run"] == k])
                                                             ["confidently_wrong_pct"] for k in RUNS],
                           **latency(sub)}
        fresh = [r for r in recs if not (version == "v3" and r["set"] == "frozen")]
        row["cost_per_question_cad"] = cost_per_question(version, len(fresh))
        row["cost_note"] = ("v3 frozen runs predate the ledger; cost measured on its 20 fresh definition-question runs"
                            if version == "v3" else "measured on all 130 question-runs")
        if version == "v4":
            row["citations"] = citation_stats(recs)
        out["conditions"][cond] = row
    # per-question differences on the definition set, for the write-up
    v3d = {(r["id"], r["run"]): r for r in by_cond["no_rag_v3"] if r["set"] == "defs"}
    out["defs_by_question"] = [
        {"id": r["id"], "run": r["run"], "question": r["question"], "no_rag": v3d[(r["id"], r["run"])]["outcome"],
         "rag": r["outcome"], "rag_citations": r["citations"]}
        for r in by_cond["rag_v4"] if r["set"] == "defs"]
    changed = {}
    v3f = {(r["id"], r["run"]): r["outcome"] for r in by_cond["no_rag_v3"] if r["set"] == "frozen"}
    for r in by_cond["rag_v4"]:
        if r["set"] == "frozen" and r["outcome"] != v3f[(r["id"], r["run"])]:
            changed.setdefault(r["id"], []).append(f"run{r['run']}: {v3f[(r['id'], r['run'])]} -> {r['outcome']}")
    out["frozen55_changed"] = changed
    save_metrics("rag_ablation", out)
    for cond, row in out["conditions"].items():
        for subset in ("frozen55", "defs10", "all65"):
            x = row[subset]
            print(f"{cond:<10} {subset:<8} exec {x['execution_accuracy_pct']:>6}  abst {x['abstention_accuracy_pct']}  "
                  f"CW {x['confidently_wrong_pct']:>5}  p50 {x['latency_p50_s']}s  {x['outcomes']}")
    return out


if __name__ == "__main__":
    main()
