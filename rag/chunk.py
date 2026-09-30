"""Split docs into chunks for embedding.

Paragraph-aware: a doc is split on blank lines (headings, paragraphs, tables and code blocks stay
whole), then paragraphs are packed greedily into chunks of at most `size` words. A paragraph longer
than `size` is split into word windows with `overlap` words of overlap. Every chunk is prefixed with
the doc title, so a chunk that says "the 90-day window" still says what it is about.
size=None keeps each doc as one chunk.
"""
import re
from dataclasses import dataclass

from rag.docs import Doc


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    doc_id: str
    index: int
    text: str


def _paragraphs(body: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]


def _windows(words: list[str], size: int, overlap: int) -> list[list[str]]:
    step = max(1, size - overlap)
    return [words[i:i + size] for i in range(0, max(1, len(words) - overlap), step)]


def chunk_doc(doc: Doc, size: int | None, overlap: int = 16) -> list[Chunk]:
    header = f"{doc.title}."
    if size is None:
        pieces = [doc.body]
    else:
        pieces, current = [], []
        for para in _paragraphs(doc.body):
            words = para.split()
            if len(words) > size:
                if current:
                    pieces.append(" ".join(current))
                    current = []
                pieces += [" ".join(w) for w in _windows(words, size, overlap)]
            elif len(current) + len(words) > size:
                pieces.append(" ".join(current))
                current = words
            else:
                current += words
        if current:
            pieces.append(" ".join(current))
    return [Chunk(f"{doc.id}#{i}", doc.id, i, f"{header}\n{p}") for i, p in enumerate(pieces)]


def chunk_all(docs: list[Doc], size: int | None, overlap: int = 16) -> list[Chunk]:
    return [c for d in docs for c in chunk_doc(d, size, overlap)]
