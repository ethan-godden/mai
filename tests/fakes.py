"""Test doubles shared across test modules."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pydantic import BaseModel

from mai.config import Tier
from mai.llm import Completion, Message, StructuredOutputError, StructuredResult, TextCallback
from mai.search import SearchError, SearchResult


def completion(text: str, tier: Tier = "main") -> Completion:
    return Completion(
        text=text, tier=tier, model="fake", finish_reason="stop",
        prompt_tokens=100, completion_tokens=50, duration_s=1.0, ttft_s=0.2,
    )  # fmt: skip


class FakeLLM:
    """Scripted model. ``structured`` answers by schema; ``complete`` returns ``answer``.

    Every call is recorded in ``calls`` as (kind, tier, messages).
    """

    def __init__(
        self,
        structured: Mapping[Any, Any] | None = None,
        answer: str | Callable[[Sequence[Message]], str] = "",
    ) -> None:
        self._structured = structured or {}
        self._answer = answer
        self.calls: list[tuple[str, Tier, list[Message]]] = []

    async def complete(
        self,
        tier: Tier,
        messages: Sequence[Message],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        on_text: TextCallback | None = None,
    ) -> Completion:
        self.calls.append(("complete", tier, list(messages)))
        text = self._answer(messages) if callable(self._answer) else self._answer
        if on_text:
            for word in text.split(" "):
                on_text(word + " ")
        return completion(text, tier)

    async def structured[T: BaseModel](
        self,
        tier: Tier,
        messages: Sequence[Message],
        schema: type[T],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> StructuredResult[T]:
        self.calls.append(("structured", tier, list(messages)))
        value = self._structured.get(schema)
        if value is None:
            raise StructuredOutputError(schema.__name__, "junk", "scripted failure")
        return StructuredResult(
            value=value, completion=completion("", tier), attempts=1, constrained=True
        )


class StubProvider:
    """Search provider returning fixed results, or raising, per query."""

    def __init__(
        self,
        name: str = "stub",
        results: dict[str, list[SearchResult]] | list[SearchResult] | None = None,
        error: str | None = None,
    ) -> None:
        self.name = name
        self._results = results or []
        self._error = error
        self.queries: list[str] = []

    async def search(self, query: str, *, max_results: int = 10) -> list[SearchResult]:
        self.queries.append(query)
        if self._error:
            raise SearchError(self.name, self._error)
        if isinstance(self._results, dict):
            return self._results.get(query, [])[:max_results]
        return self._results[:max_results]


class FakeEmbedder:
    """Deterministic bag-of-words embedder: shared words give similar vectors.

    Words in ``synonyms`` map to the same dimension, which lets tests show a
    dense match where there is no keyword overlap.
    """

    name = "fake-embedder"

    def __init__(self, synonyms: dict[str, str] | None = None, dimensions: int = 64) -> None:
        self._synonyms = synonyms or {}
        self._dimensions = dimensions
        self.passage_calls: list[list[str]] = []

    def _vector(self, text: str):
        import hashlib
        import re

        import numpy as np

        vector = np.zeros(self._dimensions, dtype=np.float32)
        for raw in re.findall(r"\w+", text.lower()):
            word = str(self._synonyms.get(raw, raw))
            vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % self._dimensions] += 1.0
        norm = float(np.linalg.norm(vector))
        return vector / norm if norm else vector

    def embed_passages(self, texts):
        import numpy as np

        self.passage_calls.append(list(texts))
        return np.stack([self._vector(text) for text in texts])

    def embed_query(self, text):
        return self._vector(text)
