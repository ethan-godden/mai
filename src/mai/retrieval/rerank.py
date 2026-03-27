"""Cross-encoder reranking.

An embedding model encodes the question and each passage separately, which is
fast but coarse. A cross-encoder reads the question and one passage together
and scores how well the passage answers it, which is slower but markedly more
accurate. It is therefore run only on the shortlist the first stage produces.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from typing import Any, Protocol

import numpy as np

from mai.retrieval.embed import default_device


class Reranker(Protocol):
    name: str

    def score(self, query: str, texts: Sequence[str]) -> np.ndarray:
        """One relevance score per text; higher is better. Scales differ between models."""
        ...


class CrossEncoderReranker:
    """A cross-encoder from Hugging Face, loaded on first use."""

    def __init__(
        self,
        model_name: str,
        *,
        device: str | None = None,
        batch_size: int = 16,
        max_tokens: int = 512,
    ) -> None:
        self.name = model_name
        self._device = device
        self._batch_size = batch_size
        self._max_tokens = max_tokens
        self._model: Any = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        with self._lock:
            if self._model is None:
                from sentence_transformers import CrossEncoder

                self._model = CrossEncoder(
                    self.name, device=self._device or default_device(), max_length=self._max_tokens
                )
            return self._model

    def score(self, query: str, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros(0, dtype=np.float32)
        scores = self._load().predict(
            [(query, text) for text in texts],
            batch_size=self._batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return np.asarray(scores, dtype=np.float32).reshape(-1)
