"""Build the pgvector index for every chunking configuration.

    python -m rag.index            # all configurations, local MiniLM embeddings
"""
from rag import store
from rag.chunk import chunk_all
from rag.docs import load_docs
from rag.embed import default_embedder

# chunk size in words (None = one chunk per doc); overlap 16 words when a paragraph is split
CONFIGS: dict[str, int | None] = {"w32": 32, "w64": 64, "w128": 128, "doc": None}


def build(embedder: object | None = None, dsn: str | None = None,
          configs: dict[str, int | None] = CONFIGS) -> dict[str, dict]:
    embedder = embedder or default_embedder()
    docs = load_docs()
    out = {}
    with store.connect(dsn) as conn:
        for name, size in configs.items():
            chunks = chunk_all(docs, size)
            vectors = embedder.encode([c.text for c in chunks])
            store.write_config(conn, name, docs, chunks, vectors, embedder.name)
            words = [len(c.text.split()) for c in chunks]
            out[name] = {"chunk_words": size, "chunks": len(chunks), "docs": len(docs),
                         "mean_words": round(sum(words) / len(words), 1), "max_words": max(words)}
            print(f"{name:>5}: {len(chunks):>3} chunks from {len(docs)} docs, "
                  f"mean {out[name]['mean_words']} words, max {out[name]['max_words']}")
    return out


if __name__ == "__main__":
    build()
