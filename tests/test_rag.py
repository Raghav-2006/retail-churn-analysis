"""Knowledge base, chunking, pgvector retrieval and the spend guard. No model download, no API call:
retrieval tests use a deterministic hashed bag-of-words embedder."""
import json
from types import SimpleNamespace

import pytest
import yaml

from analyst import budget
from analyst.evaluate import eval_set_sha, load_eval_set
from rag.chunk import chunk_all, chunk_doc
from rag.docs import KNOWLEDGE, load_docs
from rag.embed import HashEmbedder
from rag.evaluate import RELEVANCE, score
from rag.retrieve import format_context, ranked_docs

EVAL_SET_DEFS_SHA256 = "8c4e1723bf8a91a873061a89f45ed7c9fd24fa4340415c9347abdcc3ae7282bc"


def test_knowledge_base_shape():
    docs = load_docs()
    assert 25 <= len(docs) <= 40
    assert len({d.id for d in docs}) == len(docs)
    deprecated = [d for d in docs if d.deprecated]
    assert {d.id for d in deprecated} == {"metric-churn-2010", "metric-active-customer-2009", "metric-aov-legacy"}
    ids = {d.id for d in docs}
    assert all(d.superseded_by in ids and not by_id(docs, d.superseded_by).deprecated for d in deprecated)
    # the one conflicting-but-not-flagged doc is older than the definitions that contradict it
    faq = by_id(docs, "sales-team-faq-2010")
    assert faq.doc_type == "faq" and not faq.deprecated
    assert faq.last_updated < by_id(docs, "metric-large-order").last_updated


def by_id(docs, doc_id):
    return next(d for d in docs if d.id == doc_id)


def test_chunking_respects_size_and_keeps_the_title():
    docs = load_docs()
    whole = chunk_all(docs, None)
    assert len(whole) == len(docs)
    small = chunk_all(docs, 32)
    assert len(small) > len(docs)
    for c in small:
        title = by_id(docs, c.doc_id).title
        assert c.text.startswith(title)
        assert len(c.text.split()) <= 32 + len(title.split())
    # every word of every doc survives chunking
    doc = by_id(docs, "metric-churn")
    assert set(doc.body.split()) <= set(" ".join(c.text for c in chunk_doc(doc, 32)).split())


def test_definition_eval_set_is_frozen_and_separate():
    assert eval_set_sha("defs") == EVAL_SET_DEFS_SHA256
    items = load_eval_set("defs")
    assert len(items) == 10 and all(it["difficulty"] == "definition" and "sql" in it for it in items)
    assert not {it["id"] for it in items} & {it["id"] for it in load_eval_set("frozen")}


def test_relevance_labels_cover_every_question_and_exist():
    rel = yaml.safe_load(RELEVANCE.read_text())
    ids = {it["id"] for s in ("frozen", "defs") for it in load_eval_set(s)}
    assert set(rel) == ids
    docs = {p.stem for p in KNOWLEDGE.glob("*.md")}
    assert all(d in docs for v in rel.values() for d in v["relevant"] + v.get("traps", []))


def test_retrieval_metrics():
    s = score(["a", "b", "c"], relevant=["b", "z"], traps=["a"])
    assert s["recall"] == 0.5 and s["hit"] == 1.0 and s["rr"] == 0.5
    assert s["trap_in_context"] == 1.0 and s["trap_above_relevant"] == 1.0
    assert score(["x"], ["b"], [])["rr"] == 0.0


def test_pgvector_index_and_retrieval(pg_dsn):
    import psycopg

    from rag.index import build
    from rag.retrieve import Retriever

    emb = HashEmbedder()
    out = build(emb, pg_dsn, configs={"test_doc": None, "test_w32": 32})
    assert out["test_doc"]["chunks"] == len(load_docs())
    r = Retriever(config="test_doc", k=5, policy="include", embedder=emb, dsn=pg_dsn)
    hits = r.retrieve("What was the cancellation rate, cancelled revenue over gross sales revenue?")
    assert hits[0].doc_id == "metric-cancellation-rate"
    assert [h.similarity for h in hits] == sorted((h.similarity for h in hits), reverse=True)
    # exclude never returns deprecated docs; penalize ranks the current churn doc above the old one
    q = "churn definition: customer has no order in the days before the reporting date"
    assert not any(h.deprecated for h in Retriever("test_doc", 8, "exclude", emb, pg_dsn).retrieve(q))
    pen = ranked_docs(Retriever("test_doc", 8, "penalize", emb, pg_dsn).retrieve(q))
    if "metric-churn-2010" in pen:
        assert pen.index("metric-churn") < pen.index("metric-churn-2010")
    ctx = format_context(Retriever("test_doc", 8, "include", emb, pg_dsn).retrieve(q))
    assert "DEPRECATED, superseded by metric-churn" in ctx
    with psycopg.connect(pg_dsn) as conn:
        conn.execute("DELETE FROM knowledge.chunks WHERE config LIKE 'test_%'")


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(budget, "LEDGER", tmp_path / "ledger.json")
    monkeypatch.setattr(budget, "CAP_CAD", 0.01)
    return tmp_path / "ledger.json"


def test_budget_records_usage_and_stops_at_the_cap(ledger):
    m = "gemini-3.1-flash-lite"
    assert budget.call_cost_cad(m, 1_000_000, 0) == pytest.approx(0.25 * budget.USD_TO_CAD)
    assert budget.call_cost_cad(m, 0, 1_000_000) == pytest.approx(1.50 * budget.USD_TO_CAD)
    usage = SimpleNamespace(prompt_token_count=2000, candidates_token_count=100, thoughts_token_count=50)
    cost = budget.record(m, "v4:agent", usage)
    led = json.loads(ledger.read_text())
    assert led["calls"] == 1 and led["thought_tokens"] == 50 and led["est_cad"] == pytest.approx(cost, abs=1e-6)
    budget.check(m)                                     # far below the cap
    with pytest.raises(budget.BudgetExceeded):
        budget.estimate("too big", 1000, m, 3000, 300)  # projected past the CA$0.01 test cap
    for _ in range(3):
        budget.record(m, "v4:agent", usage)
    with pytest.raises(budget.BudgetExceeded):
        budget.check(m)


def test_budget_refuses_unpriced_models(ledger):
    with pytest.raises(ValueError):
        budget.check("gemini-3.5-flash")
