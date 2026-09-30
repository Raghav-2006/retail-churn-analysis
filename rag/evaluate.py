"""Retrieval eval: recall@k and MRR for every chunk size x k x deprecation policy. No LLM calls.

    python -m rag.evaluate          # writes metrics/rag_retrieval.json

Relevance labels: knowledge/relevance.yaml (hand-labelled, doc level) for all 65 questions.
Ranking is doc level: the top-k CHUNKS the agent would see, reduced to their docs in rank order.
  recall@k  share of a question's relevant docs that appear in its top-k chunks (mean over questions)
  hit@k     share of questions with at least one relevant doc in the top-k
  MRR@k     mean of 1 / rank of the first relevant doc (0 if none in the top-k)
  trap@k    definition questions only: share whose top-k contains a deprecated/conflicting doc
            listed as a trap, and share where a trap doc ranks above the first relevant doc
"""
from pathlib import Path

import yaml

from rag.index import CONFIGS, build
from rag.retrieve import POLICIES, Retriever, ranked_docs

ROOT = Path(__file__).resolve().parents[1]
RELEVANCE = ROOT / "knowledge" / "relevance.yaml"
KS = (3, 5, 8)
SUBSETS = {
    "all": lambda it: True,
    "frozen55": lambda it: it["set"] == "frozen55",
    "defs": lambda it: it["set"] == "defs",
    "answerable": lambda it: it["difficulty"] != "unanswerable",
    "unanswerable": lambda it: it["difficulty"] == "unanswerable",
}


def questions() -> list[dict]:
    rel = yaml.safe_load(RELEVANCE.read_text())
    items = []
    for name in ("eval_set.yaml", "eval_set_defs.yaml"):
        for it in yaml.safe_load((ROOT / "analyst" / name).read_text()):
            items.append({"id": it["id"], "question": it["question"], "difficulty": it["difficulty"],
                          "set": "defs" if name.endswith("defs.yaml") else "frozen55",
                          "relevant": rel[it["id"]]["relevant"], "traps": rel[it["id"]].get("traps", [])})
    return items


def score(docs: list[str], relevant: list[str], traps: list[str]) -> dict:
    ranks = [docs.index(d) + 1 for d in relevant if d in docs]
    first = min(ranks) if ranks else None
    trap_ranks = [docs.index(d) + 1 for d in traps if d in docs]
    return {"recall": len(ranks) / len(relevant), "hit": float(bool(ranks)), "rr": 1 / first if first else 0.0,
            "trap_in_context": float(bool(trap_ranks)),
            "trap_above_relevant": float(bool(trap_ranks) and (first is None or min(trap_ranks) < first))}


def mean(xs: list[float]) -> float:
    return round(sum(xs) / len(xs), 4) if xs else 0.0


def run(embedder: object | None = None, dsn: str | None = None, rebuild: bool = True) -> dict:
    index = build(embedder, dsn) if rebuild else {}
    items = questions()
    rows, per_question = [], {}
    for config in CONFIGS:
        for policy in POLICIES:
            r = Retriever(config=config, k=max(KS), policy=policy, embedder=embedder, dsn=dsn)
            ranked = {it["id"]: ranked_docs(r.retrieve(it["question"], k=max(KS))) for it in items}
            for k in KS:
                topk = {it["id"]: ranked_docs(r.retrieve(it["question"], k=k)) for it in items}
                for subset in SUBSETS:
                    sub = [it for it in items if SUBSETS[subset](it)]
                    s = [score(topk[it["id"]], it["relevant"], it["traps"]) for it in sub]
                    row = {"config": config, "policy": policy, "k": k, "subset": subset, "questions": len(sub),
                           "recall": mean([x["recall"] for x in s]), "hit": mean([x["hit"] for x in s]),
                           "mrr": mean([x["rr"] for x in s])}
                    if subset == "defs":
                        row["trap_in_context"] = mean([x["trap_in_context"] for x in s])
                        row["trap_above_relevant"] = mean([x["trap_above_relevant"] for x in s])
                    rows.append(row)
            if policy == "penalize":
                per_question[config] = ranked
            r.conn.close()
    choice = choose(rows)
    misses = [{"id": it["id"], "question": it["question"], "relevant": it["relevant"],
               "top_docs": per_question[choice["config"]][it["id"]][:choice["k"]]}
              for it in items
              if not set(it["relevant"]) & set(per_question[choice["config"]][it["id"]][:choice["k"]])]
    return {"embedder": getattr(embedder, "name", None) or "sentence-transformers/all-MiniLM-L6-v2",
            "index": index, "ks": list(KS), "results": rows, "chosen": choice, "misses_at_chosen": misses}


def choose(rows: list[dict]) -> dict:
    """Pre-registered rule for the agent's setting, applied to the penalize policy on all 65 questions:
    the chunk size with the best recall@8, then the smallest k whose recall is within 0.02 of that
    size's recall@8 (a shorter prompt for the same coverage). Ties -> higher MRR."""
    cand = [r for r in rows if r["subset"] == "all" and r["policy"] == "penalize"]
    best8 = max((r for r in cand if r["k"] == max(KS)), key=lambda r: (r["recall"], r["mrr"]))
    same = sorted((r for r in cand if r["config"] == best8["config"]), key=lambda r: r["k"])
    pick = next(r for r in same if r["recall"] >= best8["recall"] - 0.02)
    return {"config": pick["config"], "k": pick["k"], "policy": "penalize", "recall": pick["recall"], "mrr": pick["mrr"]}


def main() -> None:
    from src.metrics import save_metrics

    res = run()
    save_metrics("rag_retrieval", res)
    for subset in ("all", "answerable", "unanswerable", "defs"):
        print(f"-- {subset} (penalize)")
        for r in res["results"]:
            if r["subset"] == subset and r["policy"] == "penalize":
                print(f"{r['config']:>5} k={r['k']}  recall {r['recall']:.3f}  hit {r['hit']:.3f}  MRR {r['mrr']:.3f}")
    print("defs subset, trap docs (k=5):")
    for r in res["results"]:
        if r["subset"] == "defs" and r["k"] == 5:
            print(f"{r['config']:>5} {r['policy']:<9} recall {r['recall']:.2f}  trap in context {r['trap_in_context']:.2f}"
                  f"  trap above relevant {r['trap_above_relevant']:.2f}")
    print("chosen:", res["chosen"])
    print(f"misses at chosen setting: {len(res['misses_at_chosen'])}")
    for m in res["misses_at_chosen"]:
        print(f"  {m['id']} {m['question'][:70]!r} want {m['relevant']} got {m['top_docs']}")


if __name__ == "__main__":
    main()
