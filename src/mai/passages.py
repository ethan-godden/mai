"""Split documents into passages and pick the ones worth reading.

This is the M3 baseline: keyword ranking with BM25. Milestone M4 replaces the
ranking with hybrid dense retrieval and a reranker (docs/ML.md); the
``Passage`` type and the selection rules stay.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace

from rank_bm25 import BM25Okapi

from mai.extract import Document

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"\w+")
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does", "for", "from",
        "how", "in", "is", "it", "of", "on", "or", "that", "the", "to", "was", "what", "when",
        "where", "which", "who", "why", "with",
    }
)  # fmt: skip


@dataclass(frozen=True)
class Passage:
    source: int
    """Index of the document this passage came from, in the list given to the chunker."""
    position: int
    """Order of the passage within its document, from 0."""
    text: str
    heading: str = ""
    """The nearest heading above the passage, for context."""
    score: float = 0.0


def tokenize(text: str) -> list[str]:
    return [word for word in _WORD.findall(text.lower()) if word not in _STOPWORDS]


def _split_long(block: str, limit: int) -> list[str]:
    """Break an oversized block at sentence ends; fall back to a hard word split."""
    pieces: list[str] = []
    current: list[str] = []
    count = 0
    for sentence in _SENTENCE_END.split(block):
        words = sentence.split()
        while len(words) > limit:  # a single "sentence" longer than a passage
            if current:
                pieces.append(" ".join(current))
                current, count = [], 0
            pieces.append(" ".join(words[:limit]))
            words = words[limit:]
        if count + len(words) > limit and current:
            pieces.append(" ".join(current))
            current, count = [], 0
        current.extend(words)
        count += len(words)
    if current:
        pieces.append(" ".join(current))
    return pieces


def chunk_document(document: Document, source: int, *, max_words: int = 220) -> list[Passage]:
    """Split a document's Markdown into passages of at most ``max_words`` words.

    Paragraphs are kept whole where possible and grouped under their heading;
    a passage never spans two headings.
    """
    passages: list[Passage] = []
    heading = ""
    buffer: list[str] = []
    words = 0

    def flush() -> None:
        nonlocal buffer, words
        if buffer:
            passages.append(Passage(source, len(passages), "\n\n".join(buffer), heading))
        buffer, words = [], 0

    for block in re.split(r"\n\s*\n", document.text):
        block = block.strip()
        if not block:
            continue
        if block.startswith("#"):
            flush()
            heading = block.lstrip("#").strip()
            continue
        for piece in _split_long(block, max_words) if len(block.split()) > max_words else [block]:
            size = len(piece.split())
            if words + size > max_words:
                flush()
            buffer.append(piece)
            words += size
    flush()
    return passages


def rank_bm25(passages: Sequence[Passage], query: str) -> list[tuple[int, float]]:
    """Rank passages by BM25 keyword relevance: (index, score) pairs, best first.

    Passages sharing no words with the query are left out entirely. BM25 can
    score a real match at or below zero when a word occurs in most passages,
    so relevance is decided by word overlap, not by the sign of the score.
    """
    if not passages:
        return []
    query_terms = set(tokenize(query))
    corpus = [tokenize(f"{passage.heading} {passage.text}") for passage in passages]
    scores = BM25Okapi(corpus).get_scores(list(query_terms))
    ranked = sorted(range(len(passages)), key=lambda index: scores[index], reverse=True)
    return [(i, float(scores[i])) for i in ranked if query_terms.intersection(corpus[i])]


def select_passages(
    passages: Sequence[Passage],
    query: str,
    *,
    max_passages: int = 10,
    per_source: int = 3,
) -> list[Passage]:
    """Return the passages most relevant to ``query`` by keyword match, best first.

    At most ``per_source`` passages come from any one document, so a single
    long page cannot crowd out the others.
    """
    chosen: list[Passage] = []
    taken: dict[int, int] = {}
    for index, score in rank_bm25(passages, query):
        passage = passages[index]
        if taken.get(passage.source, 0) >= per_source:
            continue
        taken[passage.source] = taken.get(passage.source, 0) + 1
        chosen.append(replace(passage, score=score))
        if len(chosen) == max_passages:
            break
    return chosen
