"""Ranking methods compared by the retrieval evaluation.

Each entry builds a ``Method``: a function from (question, search queries,
passages) to passage indices, best first. Building is lazy because the ML
methods load models.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from functools import cache

from mai.config import load_settings
from mai.eval.retrieval import Method
from mai.passages import Passage, rank_bm25
from mai.retrieval.embed import CachedEmbedder, Embedder, SentenceTransformerEmbedder
from mai.retrieval.rank import fuse, passage_text, rank_dense
from mai.retrieval.rerank import CrossEncoderReranker, Reranker


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


BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
EMBEDDERS: dict[str, tuple[str, str]] = {
    "bge-small": ("BAAI/bge-small-en-v1.5", BGE_QUERY_PREFIX),
    "qwen3-0.6b": ("Qwen/Qwen3-Embedding-0.6B", ""),
}
"""Short name -> (Hugging Face model, query prefix)."""


@cache
def embedder(short_name: str) -> Embedder:
    model, prefix = EMBEDDERS[short_name]
    cache_path = load_settings().paths.data_dir / "cache" / "embeddings.sqlite"
    return CachedEmbedder(SentenceTransformerEmbedder(model, query_prefix=prefix), cache_path)


def dense(short_name: str) -> Callable[[], Method]:
    """Embedding similarity between the question and each passage."""

    def build() -> Method:
        model = embedder(short_name)

        def rank(question: str, queries: Sequence[str], passages: Sequence[Passage]) -> list[int]:
            ranked, _ = rank_dense(model, passages, question)
            return [index for index, _ in ranked]

        return rank

    return build


def hybrid(short_name: str) -> Callable[[], Method]:
    """Keyword and embedding rankings combined with reciprocal rank fusion."""

    def build() -> Method:
        model = embedder(short_name)

        def rank(question: str, queries: Sequence[str], passages: Sequence[Passage]) -> list[int]:
            keyword = [index for index, _ in rank_bm25(passages, " ".join([question, *queries]))]
            semantic, _ = rank_dense(model, passages, question)
            return _fill(fuse([keyword, [index for index, _ in semantic]]), len(passages))

        return rank

    return build


RERANKERS: dict[str, str] = {
    "minilm": "cross-encoder/ms-marco-MiniLM-L6-v2",
    "bge-v2-m3": "BAAI/bge-reranker-v2-m3",
}
FIRST_STAGE = "bge-small"
"""The embedder used for the shortlist that rerankers reorder."""
SHORTLIST = 40


@cache
def reranker(short_name: str) -> Reranker:
    return CrossEncoderReranker(RERANKERS[short_name])


def rerank(short_name: str) -> Callable[[], Method]:
    """Hybrid shortlist reordered by a cross-encoder reading question and passage together."""

    def build() -> Method:
        shortlist = hybrid(FIRST_STAGE)()
        model = reranker(short_name)

        def rank(question: str, queries: Sequence[str], passages: Sequence[Passage]) -> list[int]:
            candidates = shortlist(question, queries, passages)[:SHORTLIST]
            scores = model.score(question, [passage_text(passages[i]) for i in candidates])
            order = sorted(range(len(candidates)), key=lambda i: scores[i], reverse=True)
            return _fill([candidates[i] for i in order], len(passages))

        return rank

    return build


METHODS: dict[str, Callable[[], Method]] = {
    "bm25": bm25,
    **{f"dense:{name}": dense(name) for name in EMBEDDERS},
    **{f"hybrid:{name}": hybrid(name) for name in EMBEDDERS},
    **{f"rerank:{name}": rerank(name) for name in RERANKERS},
}
