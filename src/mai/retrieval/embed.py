"""Text embeddings for dense retrieval.

An embedding model maps text to a vector such that texts with similar meaning
land close together, which finds relevant passages that share no words with
the question. Models run in this process through sentence-transformers, on
the Apple GPU when available.

Embedding is synchronous and compute-bound: async callers should use a thread.
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

import numpy as np

Vectors = np.ndarray
"""Shape (n, dimensions), float32, each row scaled to unit length."""


class Embedder(Protocol):
    name: str

    def embed_passages(self, texts: Sequence[str]) -> Vectors: ...

    def embed_query(self, text: str) -> np.ndarray:
        """One unit-length vector. Queries may be encoded differently from passages."""
        ...


def default_device() -> str:
    import torch

    return "mps" if torch.backends.mps.is_available() else "cpu"


class SentenceTransformerEmbedder:
    """An embedding model from Hugging Face, loaded on first use."""

    def __init__(
        self,
        model_name: str,
        *,
        query_prefix: str = "",
        device: str | None = None,
        batch_size: int = 32,
        max_tokens: int = 512,
    ) -> None:
        self.name = model_name
        self._query_prefix = query_prefix
        self._device = device
        self._batch_size = batch_size
        self._max_tokens = max_tokens
        self._model: Any = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        with self._lock:
            if self._model is None:
                from sentence_transformers import SentenceTransformer

                model = SentenceTransformer(self.name, device=self._device or default_device())
                model.max_seq_length = min(
                    model.max_seq_length or self._max_tokens, self._max_tokens
                )
                self._model = model
            return self._model

    def _encode(self, texts: Sequence[str], **kwargs: Any) -> Vectors:
        vectors = self._load().encode(
            list(texts),
            batch_size=self._batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
            **kwargs,
        )
        return np.asarray(vectors, dtype=np.float32)

    def embed_passages(self, texts: Sequence[str]) -> Vectors:
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        return self._encode(texts)

    def embed_query(self, text: str) -> np.ndarray:
        # Many models are trained with an instruction on the query side only.
        # Newer ones ship it as a named prompt; older ones document a prefix.
        if "query" in (getattr(self._load(), "prompts", None) or {}):
            return self._encode([text], prompt_name="query")[0]
        return self._encode([self._query_prefix + text])[0]


class CachedEmbedder:
    """Wraps an embedder with an on-disk cache of passage vectors.

    Keyed by model name and a hash of the text, so re-reading a cached page, or
    seeing the same passage in another run, costs nothing. Queries are not
    cached: they are short and rarely repeat.
    """

    def __init__(self, inner: Embedder, path: Path) -> None:
        self.name = inner.name
        self._inner = inner
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS embeddings ("
            "model TEXT NOT NULL, sha TEXT NOT NULL, vector BLOB NOT NULL, "
            "PRIMARY KEY (model, sha))"
        )

    def close(self) -> None:
        self._db.close()

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()

    def embed_passages(self, texts: Sequence[str]) -> Vectors:
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        keys = [self._key(text) for text in texts]
        found: dict[str, np.ndarray] = {}
        with self._lock:
            for start in range(0, len(keys), 500):  # stay under SQLite's parameter limit
                chunk = keys[start : start + 500]
                marks = ",".join("?" * len(chunk))
                rows = self._db.execute(
                    f"SELECT sha, vector FROM embeddings WHERE model = ? AND sha IN ({marks})",
                    [self.name, *chunk],
                )
                found.update((sha, np.frombuffer(blob, dtype=np.float32)) for sha, blob in rows)

        missing = {key: text for key, text in zip(keys, texts, strict=True) if key not in found}
        if missing:
            vectors = self._inner.embed_passages(list(missing.values()))
            with self._lock, self._db:
                self._db.executemany(
                    "INSERT OR REPLACE INTO embeddings VALUES (?, ?, ?)",
                    [
                        (self.name, key, vector.astype(np.float32).tobytes())
                        for key, vector in zip(missing, vectors, strict=True)
                    ],
                )
            found.update(zip(missing, vectors, strict=True))
        return np.stack([found[key] for key in keys])

    def embed_query(self, text: str) -> np.ndarray:
        return self._inner.embed_query(text)
