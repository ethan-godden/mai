"""Ranking methods compared by the retrieval evaluation.

Each entry builds a ``Method``: a function from (question, search queries,
passages) to passage indices, best first. Building is lazy because the ML
methods load models.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from mai.eval.retrieval import Method
from mai.passages import Passage, rank_bm25


def _fill(ranked: list[int], total: int) -> list[int]:
    """Append unranked passages in document order, so every method returns a full ordering."""
    seen = set(ranked)
    return ranked + [index for index in range(total) if index not in seen]


def bm25() -> Method:
    """The M3 baseline: keyword ranking over the question and its search queries."""

    def rank(question: str, queries: Sequence[str], passages: Sequence[Passage]) -> list[int]:
        ranked = [index for index, _ in rank_bm25(passages, " ".join([question, *queries]))]
        return _fill(ranked, len(passages))

    return rank


METHODS: dict[str, Callable[[], Method]] = {"bm25": bm25}
