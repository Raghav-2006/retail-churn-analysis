"""Retrieve the top-k knowledge chunks for a question, with a preference for current docs.

Deprecation policies (compared in rag/evaluate.py):
  include   rank by cosine similarity only;
  penalize  subtract DEPRECATED_PENALTY from a deprecated chunk's similarity (default);
  exclude   never return deprecated chunks.
Deprecated chunks that are still returned are labelled in the prompt with their replacement.
"""
from dataclasses import dataclass

import psycopg

from rag import store
from rag.embed import default_embedder

DEFAULT_CONFIG = "doc"         # chosen by the retrieval eval (metrics/rag_retrieval.json): one chunk per doc
DEFAULT_K = 8
DEFAULT_POLICY = "penalize"
DEPRECATED_PENALTY = 0.10
POLICIES = ("include", "penalize", "exclude")


@dataclass
class Hit:
    chunk_id: str
    doc_id: str
    title: str
    doc_type: str
    last_updated: str
    deprecated: bool
    superseded_by: str | None
    text: str
    similarity: float
    score: float


class Retriever:
    def __init__(self, config: str = DEFAULT_CONFIG, k: int = DEFAULT_K, policy: str = DEFAULT_POLICY,
                 embedder: object | None = None, dsn: str | None = None) -> None:
        if policy not in POLICIES:
            raise ValueError(f"policy must be one of {POLICIES}")
        self.config, self.k, self.policy = config, k, policy
        self.embedder = embedder or default_embedder()
        self.dsn = dsn
        self._conn: psycopg.Connection | None = None

    @property
    def conn(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed:
            self._conn = store.connect(self.dsn)
        return self._conn

    def retrieve(self, question: str, k: int | None = None) -> list[Hit]:
        k = k or self.k
        qvec = self.embedder.encode([question])[0]
        # over-fetch so a penalised deprecated chunk can be overtaken by current ones
        rows = store.search(self.conn, self.config, qvec, limit=4 * k + 8,
                            exclude_deprecated=self.policy == "exclude")
        self.conn.rollback()
        hits = [Hit(chunk_id=r["chunk_id"], doc_id=r["doc_id"], title=r["title"], doc_type=r["doc_type"],
                    last_updated=str(r["last_updated"]), deprecated=r["deprecated"], superseded_by=r["superseded_by"],
                    text=r["text"], similarity=float(r["similarity"]),
                    score=float(r["similarity"]) - (DEPRECATED_PENALTY if self.policy == "penalize" and r["deprecated"]
                                                     else 0.0))
                for r in rows]
        hits.sort(key=lambda h: (-h.score, h.chunk_id))
        return hits[:k]


def ranked_docs(hits: list[Hit]) -> list[str]:
    """Doc ids in rank order, first occurrence only (doc-level ranking for recall@k and MRR)."""
    seen: list[str] = []
    for h in hits:
        if h.doc_id not in seen:
            seen.append(h.doc_id)
    return seen


def format_context(hits: list[Hit]) -> str:
    """The retrieved knowledge as it is shown to the model, one block per chunk."""
    blocks = []
    for h in hits:
        status = f"DEPRECATED, superseded by {h.superseded_by}" if h.deprecated else "current"
        blocks.append(f"[doc: {h.doc_id} | {h.doc_type} | updated {h.last_updated} | {status}]\n{h.text}")
    return "\n\n".join(blocks)
