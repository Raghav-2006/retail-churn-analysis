"""Embeddings. Local and free: sentence-transformers all-MiniLM-L6-v2 (384 dimensions), no API.

HashEmbedder is a deterministic bag-of-words stand-in with the same interface, used by the tests
so CI does not download a model or install torch.
"""
import hashlib
import re

import numpy as np

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
DIM = 384


class LocalEmbedder:
    name = MODEL_NAME
    dim = DIM

    def __init__(self, model_name: str = MODEL_NAME) -> None:
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(model_name, device="cpu")
        self.max_tokens = self.model.max_seq_length   # longer inputs are truncated (256 word pieces)

    def encode(self, texts: list[str]) -> np.ndarray:
        return np.asarray(self.model.encode(texts, normalize_embeddings=True, batch_size=32), dtype=np.float32)


class HashEmbedder:
    """Hashed bag of lower-cased words, L2-normalised. Only for tests."""
    name = "hash-bow"
    dim = DIM

    def encode(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in re.findall(r"[a-z0-9]+", t.lower()):
                out[i, int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dim] += 1.0
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.where(norms == 0, 1, norms)


_default: LocalEmbedder | None = None


def default_embedder() -> LocalEmbedder:
    global _default
    if _default is None:
        _default = LocalEmbedder()
    return _default
