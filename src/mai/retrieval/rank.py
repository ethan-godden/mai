"""Ranking primitives: dense similarity and rank fusion."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from mai.passages import Passage
from mai.retrieval.embed import Embedder

RRF_K = 60


def passage_text(passage: Passage) -> str:
    """What is embedded and reranked: the passage with its heading for context."""
    return f"{passage.heading}\n{passage.text}" if passage.heading else passage.text


def rank_dense(
    embedder: Embedder, passages: Sequence[Passage], query: str
) -> tuple[list[tuple[int, float]], np.ndarray]:
    """Rank passages by embedding similarity to ``query``.

    Returns (index, cosine similarity) pairs, best first, and the passage
    vectors, which later stages reuse for duplicate detection and diversity.
    """
    if not passages:
        return [], np.zeros((0, 0), dtype=np.float32)
    vectors = embedder.embed_passages([passage_text(passage) for passage in passages])
    scores = vectors @ embedder.embed_query(query)  # unit vectors: dot product is cosine
    order = np.argsort(-scores, kind="stable")
    return [(int(i), float(scores[i])) for i in order], vectors


def fuse(rankings: Sequence[Sequence[int]], *, k: int = RRF_K) -> list[int]:
    """Combine rankings with reciprocal rank fusion; ties keep first-seen order."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, index in enumerate(ranking, start=1):
            scores[index] = scores.get(index, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=lambda index: scores[index], reverse=True)
