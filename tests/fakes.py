"""Test doubles shared across test modules."""

from __future__ import annotations

from collections.abc import Callable, Sequence
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
        structured: dict[type[BaseModel], Any] | None = None,
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
