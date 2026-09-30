"""pgvector store: one table, knowledge.chunks, holding every chunking configuration side by side.

Search is exact (ORDER BY embedding <=> query): a few hundred rows per configuration, so an
approximate index (HNSW/IVFFlat) would only add recall loss. Vectors are sent as pgvector text
literals, so the only requirement is the `vector` extension on the server.
"""
import numpy as np
import psycopg

from pipeline.db import get_dsn
from rag.chunk import Chunk
from rag.docs import Doc
from rag.embed import DIM

SCHEMA = "knowledge"

DDL = f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS {SCHEMA};
CREATE TABLE IF NOT EXISTS {SCHEMA}.chunks (
    config        TEXT    NOT NULL,          -- chunking configuration, e.g. 'w96'
    chunk_id      TEXT    NOT NULL,          -- '<doc_id>#<index>'
    doc_id        TEXT    NOT NULL,
    chunk_index   INTEGER NOT NULL,
    title         TEXT    NOT NULL,
    doc_type      TEXT    NOT NULL,
    owner         TEXT    NOT NULL,
    last_updated  DATE    NOT NULL,
    deprecated    BOOLEAN NOT NULL,
    superseded_by TEXT,
    text          TEXT    NOT NULL,
    embedder      TEXT    NOT NULL,
    embedding     vector({DIM}) NOT NULL,
    PRIMARY KEY (config, chunk_id)
);
"""


def to_literal(vec: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in np.asarray(vec, dtype=np.float32)) + "]"


def connect(dsn: str | None = None) -> psycopg.Connection:
    return psycopg.connect(dsn or get_dsn())


def ensure_schema(conn: psycopg.Connection) -> None:
    conn.execute(DDL)
    conn.commit()


def write_config(conn: psycopg.Connection, config: str, docs: list[Doc], chunks: list[Chunk],
                 vectors: np.ndarray, embedder: str) -> int:
    """Replace one configuration's rows in a single transaction (idempotent)."""
    by_id = {d.id: d for d in docs}
    ensure_schema(conn)
    with conn.transaction():
        conn.execute(f"DELETE FROM {SCHEMA}.chunks WHERE config = %s", (config,))
        with conn.cursor() as cur:
            cur.executemany(
                f"INSERT INTO {SCHEMA}.chunks VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector)",
                [(config, c.chunk_id, c.doc_id, c.index, by_id[c.doc_id].title, by_id[c.doc_id].doc_type,
                  by_id[c.doc_id].owner, by_id[c.doc_id].last_updated, by_id[c.doc_id].deprecated,
                  by_id[c.doc_id].superseded_by, c.text, embedder, to_literal(v))
                 for c, v in zip(chunks, vectors, strict=True)])
    return len(chunks)


def search(conn: psycopg.Connection, config: str, query_vec: np.ndarray, limit: int,
           exclude_deprecated: bool = False) -> list[dict]:
    where = "config = %(config)s" + (" AND NOT deprecated" if exclude_deprecated else "")
    cur = conn.execute(
        f"""SELECT chunk_id, doc_id, chunk_index, title, doc_type, last_updated, deprecated, superseded_by, text,
                   1 - (embedding <=> %(q)s::vector) AS similarity
            FROM {SCHEMA}.chunks WHERE {where}
            ORDER BY embedding <=> %(q)s::vector, chunk_id LIMIT %(limit)s""",
        {"config": config, "q": to_literal(query_vec), "limit": limit})
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


def configs(conn: psycopg.Connection) -> dict[str, int]:
    cur = conn.execute(f"SELECT config, count(*) FROM {SCHEMA}.chunks GROUP BY config ORDER BY config")
    return dict(cur.fetchall())
