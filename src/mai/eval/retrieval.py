"""Retrieval evaluation: does a ranking method put useful passages first?

The evaluation set is built from Mai's own pipeline so it reflects what
retrieval actually faces: for each question the real queries are generated,
the web is searched, the pages are read and split into passages. That
snapshot is the *corpus*. A ranking *method* orders each question's passages;
the top of each ordering is graded for usefulness, and the grades give the
metrics.

Grades come from the local model acting as a judge (0 to 3 per passage), not
from people. Only passages some method ranked in its top ``k`` are graded
("pooling", as in TREC): metrics at ``k`` are then fully judged for every
method compared. See docs/guide/evaluation.md for how to read the results and
what their limits are.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from mai import prompts
from mai.llm import LLM, StructuredOutputError
from mai.passages import Passage

RELEVANT = 2
"""Grades at or above this count as relevant for precision, recall and MRR."""

JUDGE_BATCH = 5

Method = Callable[[str, Sequence[str], Sequence[Passage]], list[int]]
"""(question, search queries, passages) -> passage indices, best first."""


def passage_id(url: str, text: str) -> str:
    """Stable identifier for a passage, so judgments survive rebuilding the corpus."""
    return hashlib.sha256(f"{url}\n{text}".encode()).hexdigest()[:16]


@dataclass(frozen=True)
class EvalQuestion:
    id: str
    question: str
    queries: list[str]
    passages: list[Passage]
    ids: list[str]
    """``passage_id`` of each passage, aligned with ``passages``."""
    urls: list[str]
    """URL of each source, indexed by ``Passage.source``."""


def load_corpus(path: Path) -> list[EvalQuestion]:
    questions = []
    for entry in json.loads(path.read_text()):
        passages = [Passage(**fields) for fields in entry["passages"]]
        urls = [source["url"] for source in entry["sources"]]
        questions.append(
            EvalQuestion(
                id=entry["id"],
                question=entry["question"],
                queries=entry["queries"],
                passages=passages,
                ids=[passage_id(urls[p.source], p.text) for p in passages],
                urls=urls,
            )
        )
    return questions


class Judgments:
    """Relevance grades keyed by (question id, passage id), stored as JSON lines."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._grades: dict[tuple[str, str], int] = {}
        if path.exists():
            for line in path.read_text().splitlines():
                row = json.loads(line)
                self._grades[(row["question"], row["passage"])] = row["grade"]

    def get(self, question: str, passage: str) -> int | None:
        return self._grades.get((question, passage))

    def for_question(self, question: str) -> dict[str, int]:
        return {p: grade for (q, p), grade in self._grades.items() if q == question}

    def add(self, question: str, grades: dict[str, int]) -> None:
        for passage, grade in grades.items():
            self._grades[(question, passage)] = grade
        self.save()

    def save(self) -> None:
        rows = [
            json.dumps({"question": q, "passage": p, "grade": grade})
            for (q, p), grade in sorted(self._grades.items())
        ]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("\n".join(rows) + ("\n" if rows else ""))

    def __len__(self) -> int:
        return len(self._grades)


# --- Judging -----------------------------------------------------------------------


class _Grade(BaseModel):
    passage: int
    grade: int = Field(ge=0, le=3)


class _Grades(BaseModel):
    grades: list[_Grade]


async def judge(llm: LLM, question: str, texts: Sequence[str]) -> list[int | None]:
    """Grade passages 0 to 3 for usefulness to ``question``.

    Passages are graded a few at a time. None marks a passage the judge failed
    to grade; callers should retry it rather than treat it as irrelevant.
    """
    grades: list[int | None] = [None] * len(texts)
    for start in range(0, len(texts), JUDGE_BATCH):
        batch = list(enumerate(texts[start : start + JUDGE_BATCH], start=1))
        listing = "\n\n".join(f"Passage {number}:\n{text}" for number, text in batch)
        prompt = prompts.render("judge_relevance", question=question, passages=listing)
        try:
            result = await llm.structured("fast", [{"role": "user", "content": prompt}], _Grades)
        except StructuredOutputError:
            continue
        for item in result.value.grades:
            if 1 <= item.passage <= len(batch):
                grades[start + item.passage - 1] = item.grade
    return grades


async def judge_pool(
    llm: LLM,
    question: EvalQuestion,
    indices: Sequence[int],
    judgments: Judgments,
    *,
    seed: int = 0,
) -> int:
    """Grade the passages at ``indices`` that have no judgment yet. Returns how many were graded."""
    missing = [
        i for i in dict.fromkeys(indices) if judgments.get(question.id, question.ids[i]) is None
    ]
    # Shuffled so a passage's grade does not depend on which method ranked it or where.
    random.Random(f"{seed}:{question.id}").shuffle(missing)
    texts = [_judge_text(question.passages[i]) for i in missing]
    grades = await judge(llm, question.question, texts)
    done = {
        question.ids[i]: grade
        for i, grade in zip(missing, grades, strict=True)
        if grade is not None
    }
    judgments.add(question.id, done)
    return len(done)


def _judge_text(passage: Passage) -> str:
    return (f"Section: {passage.heading}\n" if passage.heading else "") + passage.text


# --- Metrics ------------------------------------------------------------------------


def dcg(grades: Sequence[int]) -> float:
    return sum((2**grade - 1) / math.log2(rank + 1) for rank, grade in enumerate(grades, start=1))


def ndcg_at_k(ranked: Sequence[int], pool: Sequence[int], k: int) -> float:
    """Normalised discounted cumulative gain: rewards putting the best passages highest.

    ``ranked`` holds the grades of a method's ranking in order; ``pool`` holds
    every known grade for the question, from which the ideal ordering is built.
    """
    ideal = dcg(sorted(pool, reverse=True)[:k])
    return dcg(ranked[:k]) / ideal if ideal else 0.0


def precision_at_k(ranked: Sequence[int], k: int) -> float:
    return sum(grade >= RELEVANT for grade in ranked[:k]) / k


def recall_at_k(ranked: Sequence[int], pool: Sequence[int], k: int) -> float:
    relevant = sum(grade >= RELEVANT for grade in pool)
    return sum(grade >= RELEVANT for grade in ranked[:k]) / relevant if relevant else 0.0


def reciprocal_rank(ranked: Sequence[int]) -> float:
    for rank, grade in enumerate(ranked, start=1):
        if grade >= RELEVANT:
            return 1.0 / rank
    return 0.0


METRICS = ("ndcg@10", "precision@5", "precision@10", "recall@10", "mrr", "sources@10")


def score_ranking(
    question: EvalQuestion, ranking: Sequence[int], judgments: Judgments
) -> dict[str, float]:
    """Metrics for one question. Raises KeyError if a top-10 passage has no judgment."""
    top = list(ranking[:10])
    grades = []
    for index in top:
        grade = judgments.get(question.id, question.ids[index])
        if grade is None:
            raise KeyError(f"No judgment for {question.id} passage {question.ids[index]}")
        grades.append(grade)
    pool = list(judgments.for_question(question.id).values())
    return {
        "ndcg@10": ndcg_at_k(grades, pool, 10),
        "precision@5": precision_at_k(grades, 5),
        "precision@10": precision_at_k(grades, 10),
        "recall@10": recall_at_k(grades, pool, 10),
        "mrr": reciprocal_rank(grades),
        # Not a relevance metric: how many different pages the top 10 draws on.
        "sources@10": float(len({question.passages[i].source for i in top})),
    }


def mean_scores(per_question: Sequence[dict[str, float]]) -> dict[str, float]:
    return {
        metric: round(sum(scores[metric] for scores in per_question) / len(per_question), 4)
        for metric in METRICS
    }


def render_results(results: dict[str, dict[str, Any]]) -> str:
    """Markdown table of mean metrics, one row per method."""
    header = [
        "Method",
        "nDCG@10",
        "P@5",
        "P@10",
        "Recall@10",
        "MRR",
        "Sources in top 10",
        "ms / question",
    ]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for name, result in results.items():
        mean = result["mean"]
        cells = [name, *(f"{mean[m]:.3f}" for m in METRICS[:5]), f"{mean['sources@10']:.1f}"]
        cells.append(f"{result['ms_per_question']:.0f}")
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)
