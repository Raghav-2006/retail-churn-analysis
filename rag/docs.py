"""Load the knowledge base: knowledge/*.md with YAML front matter."""
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE = ROOT / "knowledge"
DOC_TYPES = {"metric_definition", "methodology", "data_caveat", "table", "policy", "faq"}


@dataclass(frozen=True)
class Doc:
    id: str
    title: str
    doc_type: str
    owner: str
    last_updated: date
    deprecated: bool
    superseded_by: str | None
    body: str


def parse(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        raise ValueError("missing front matter")
    _, front, body = text.split("---\n", 2)
    return yaml.safe_load(front), body.strip()


def load_docs(folder: Path = KNOWLEDGE) -> list[Doc]:
    docs = []
    for path in sorted(folder.glob("*.md")):
        if path.name == "README.md":
            continue
        meta, body = parse(path.read_text())
        if meta["id"] != path.stem:
            raise ValueError(f"{path.name}: id {meta['id']!r} does not match the file name")
        if meta["doc_type"] not in DOC_TYPES:
            raise ValueError(f"{path.name}: unknown doc_type {meta['doc_type']!r}")
        docs.append(Doc(id=meta["id"], title=meta["title"], doc_type=meta["doc_type"], owner=meta["owner"],
                        last_updated=meta["last_updated"], deprecated=bool(meta["deprecated"]),
                        superseded_by=meta.get("superseded_by"), body=body))
    ids = {d.id for d in docs}
    for d in docs:
        if d.deprecated and d.superseded_by not in ids:
            raise ValueError(f"{d.id}: deprecated docs must name an existing superseded_by doc")
    return docs
