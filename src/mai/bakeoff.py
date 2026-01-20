"""Model bake-off: the same small test battery run against each candidate model.

The tasks mirror what the research pipeline will ask of a model: produce
schema-valid JSON, rewrite a question into search queries, extract notes with
verbatim quotes, and answer from supplied passages while citing them (or
declining when the passages do not contain the answer).

Passages describe invented entities, so a model cannot score by recalling
facts from training; it has to read. Scoring is automatic and deliberately
simple (keyword and substring checks). It ranks candidates; it is not the
research-quality evaluation, which arrives in milestone M7.

Protocol and results: docs/MODELS.md. Runner: scripts/bakeoff.py.
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from mai.config import Tier
from mai.llm import LLM, Completion, StructuredOutputError, StructuredResult

# --- Schemas the models must fill -------------------------------------------------


class SearchPlan(BaseModel):
    queries: list[str] = Field(min_length=2, max_length=4)


class Note(BaseModel):
    claim: str
    quote: str


class NoteList(BaseModel):
    notes: list[Note] = Field(min_length=1)


class Sufficiency(BaseModel):
    verdict: Literal["sufficient", "insufficient"]
    missing: list[str]


class Section(BaseModel):
    title: str
    sub_questions: list[str] = Field(min_length=1)


class Outline(BaseModel):
    title: str
    sections: list[Section] = Field(min_length=2)


class GroundedAnswer(BaseModel):
    answerable: bool
    answer: str
    citations: list[str]


SCHEMAS: dict[str, type[BaseModel]] = {
    schema.__name__: schema for schema in (SearchPlan, NoteList, Sufficiency, Outline)
}

# --- Prompts ---------------------------------------------------------------------

REWRITE_PROMPT = (
    "Write 2 to 4 web search queries that together would find the evidence needed to "
    "answer this research question. Queries should be short, specific and distinct.\n\n"
    "Question: {question}"
)
NOTES_PROMPT = (
    "Extract every factual claim from the passage as notes. Each note has a one-sentence "
    "claim and a quote. The quote must be copied exactly, character for character, from "
    "the passage.\n\nPassage ({title}):\n{text}"
)
GROUNDED_PROMPT = (
    "Answer the question using only the passages below. Set answerable to false and leave "
    "answer empty if the passages do not contain the answer; do not use outside knowledge. "
    "In citations, list the id of each passage that supports your answer and no others.\n\n"
    "{passages}\n\nQuestion: {question}"
)

# --- Data ------------------------------------------------------------------------


@dataclass(frozen=True)
class BakeoffData:
    passages: dict[str, dict[str, Any]]
    structured: list[dict[str, Any]]
    grounded: list[dict[str, Any]]
    rewrite: list[dict[str, Any]]

    @classmethod
    def load(cls, directory: Path) -> BakeoffData:
        def read(name: str) -> list[dict[str, Any]]:
            return json.loads((directory / f"{name}.json").read_text())

        return cls(
            passages={passage["id"]: passage for passage in read("passages")},
            structured=read("structured"),
            grounded=read("grounded"),
            rewrite=read("rewrite"),
        )


# --- Scoring helpers (pure functions) ---------------------------------------------


_QUOTES = str.maketrans({chr(0x2018): "'", chr(0x2019): "'", chr(0x201C): '"', chr(0x201D): '"'})


def normalise(text: str) -> str:
    """Lower-case, drop thousands separators, and collapse whitespace and quote styles."""
    text = text.lower().translate(_QUOTES)
    text = re.sub(r"(?<=\d),(?=\d)", "", text)
    return " ".join(text.split())


def has_keyword(text: str, keyword: str) -> bool:
    """True if ``text`` contains ``keyword``; ``a|b`` accepts either alternative.

    A keyword that starts or ends with a digit must not be part of a longer
    number, so "24" does not match "2024".
    """
    haystack = normalise(text)
    for alternative in keyword.split("|"):
        needle = normalise(alternative)
        pattern = re.escape(needle)
        if needle[:1].isdigit():
            pattern = r"(?<![\d.])" + pattern
        if needle[-1:].isdigit():
            pattern += r"(?![\d])"
        if re.search(pattern, haystack):
            return True
    return False


def has_all_keywords(text: str, keywords: list[str]) -> bool:
    return all(has_keyword(text, keyword) for keyword in keywords)


def is_verbatim(quote: str, passage: str) -> bool:
    """True if the quote appears in the passage, ignoring case and whitespace."""
    cleaned = normalise(quote).strip(" \"'.")
    return bool(cleaned) and cleaned in normalise(passage)


def rate(hits: int, total: int) -> float | None:
    return round(hits / total, 3) if total else None


# --- Tasks -----------------------------------------------------------------------


@dataclass
class TaskResult:
    metrics: dict[str, float | None]
    samples: list[dict[str, Any]] = field(default_factory=list)
    """Raw outputs, kept so a person can review what the scores are based on."""


@dataclass
class Recorder:
    """Runs structured calls for one model and keeps every completion for the stats."""

    llm: LLM
    tier: Tier
    completions: list[Completion] = field(default_factory=list)
    results: list[StructuredResult[Any]] = field(default_factory=list)
    failures: int = 0

    async def ask[T: BaseModel](self, prompt: str, schema: type[T]) -> StructuredResult[T] | None:
        """Return the validated result, or None if the model never produced valid output."""
        try:
            result = await self.llm.structured(
                self.tier, [{"role": "user", "content": prompt}], schema
            )
        except StructuredOutputError:
            self.failures += 1
            return None
        self.completions.append(result.completion)
        self.results.append(result)
        return result


async def structured_task(recorder: Recorder, data: BakeoffData) -> TaskResult:
    first_try = valid = 0
    samples = []
    for case in data.structured:
        result = await recorder.ask(case["prompt"], SCHEMAS[case["schema"]])
        valid += result is not None
        first_try += result is not None and result.attempts == 1
        samples.append(
            {
                "id": case["id"],
                "attempts": result.attempts if result else None,
                "output": result.value.model_dump() if result else None,
            }
        )
    total = len(data.structured)
    return TaskResult(
        {"valid_first_try": rate(first_try, total), "valid_after_repair": rate(valid, total)},
        samples,
    )


async def rewrite_task(recorder: Recorder, data: BakeoffData) -> TaskResult:
    valid = distinct = 0
    samples = []
    for case in data.rewrite:
        result = await recorder.ask(REWRITE_PROMPT.format(question=case["question"]), SearchPlan)
        queries = result.value.queries if result else []
        valid += result is not None
        distinct += bool(queries) and len({normalise(q) for q in queries}) == len(queries)
        samples.append({"id": case["id"], "question": case["question"], "queries": queries})
    total = len(data.rewrite)
    # Usefulness of the queries is judged by a person from the saved samples.
    return TaskResult({"valid": rate(valid, total), "distinct": rate(distinct, total)}, samples)


async def notes_task(recorder: Recorder, data: BakeoffData) -> TaskResult:
    facts_found = facts_total = quotes_verbatim = quotes_total = 0
    samples = []
    for passage in data.passages.values():
        prompt = NOTES_PROMPT.format(title=passage["title"], text=passage["text"])
        result = await recorder.ask(prompt, NoteList)
        notes = result.value.notes if result else []
        for fact in passage["facts"]:
            facts_total += 1
            facts_found += any(
                has_all_keywords(f"{note.claim} {note.quote}", fact["keywords"]) for note in notes
            )
        quotes_total += len(notes)
        quotes_verbatim += sum(is_verbatim(note.quote, passage["text"]) for note in notes)
        samples.append({"id": passage["id"], "notes": [note.model_dump() for note in notes]})
    return TaskResult(
        {
            "fact_recall": rate(facts_found, facts_total),
            "quote_verbatim": rate(quotes_verbatim, quotes_total),
        },
        samples,
    )


async def grounded_task(recorder: Recorder, data: BakeoffData) -> TaskResult:
    answerable_total = unanswerable_total = 0
    correct = cited = declined = 0
    samples = []
    for case in data.grounded:
        passages = "\n\n".join(
            f"[{pid}] {data.passages[pid]['title']}\n{data.passages[pid]['text']}"
            for pid in case["passages"]
        )
        prompt = GROUNDED_PROMPT.format(passages=passages, question=case["question"])
        result = await recorder.ask(prompt, GroundedAnswer)
        answer = result.value if result else None
        if case["gold"] is None:
            unanswerable_total += 1
            declined += answer is not None and not answer.answerable
        else:
            answerable_total += 1
            if answer is not None and answer.answerable:
                correct += has_all_keywords(answer.answer, case["keywords"])
                # Strict: the supporting passage and nothing else.
                cited += [c.strip("[] ") for c in answer.citations] == [case["gold"]]
        samples.append(
            {
                "id": case["id"],
                "gold": case["gold"],
                "output": answer.model_dump() if answer else None,
            }
        )
    return TaskResult(
        {
            "answer_correct": rate(correct, answerable_total),
            "citation_correct": rate(cited, answerable_total),
            "declines_when_unsupported": rate(declined, unanswerable_total),
        },
        samples,
    )


TASKS = {
    "structured": structured_task,
    "rewrite": rewrite_task,
    "notes": notes_task,
    "grounded": grounded_task,
}

# --- Run and report ----------------------------------------------------------------


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 2) if values else None


def throughput(recorder: Recorder) -> dict[str, float | int | None]:
    """Speed figures over every successful call made through ``recorder``."""
    completions = recorder.completions
    return {
        "calls": len(completions),
        "failed_calls": recorder.failures,
        "schema_enforced": rate(
            sum(r.constrained for r in recorder.results), len(recorder.results)
        ),
        "median_tokens_per_s": _median([c.tokens_per_s for c in completions if c.tokens_per_s]),
        "median_ttft_s": _median([c.ttft_s for c in completions if c.ttft_s is not None]),
        "total_s": round(sum(c.duration_s for c in completions), 1),
        "completion_tokens": sum(c.completion_tokens or 0 for c in completions),
    }


async def run_bakeoff(llm: LLM, data: BakeoffData, *, tier: Tier = "main") -> dict[str, Any]:
    """Run every task against the model configured for ``tier``."""
    recorder = Recorder(llm, tier)
    tasks = {name: await task(recorder, data) for name, task in TASKS.items()}
    return {
        "metrics": {name: result.metrics for name, result in tasks.items()},
        "throughput": throughput(recorder),
        "samples": {name: result.samples for name, result in tasks.items()},
    }


COLUMNS: list[tuple[str, str, str]] = [
    ("JSON valid 1st try", "structured", "valid_first_try"),
    ("JSON valid", "structured", "valid_after_repair"),
    ("Fact recall", "notes", "fact_recall"),
    ("Quotes verbatim", "notes", "quote_verbatim"),
    ("Answer correct", "grounded", "answer_correct"),
    ("Citation correct", "grounded", "citation_correct"),
    ("Declines unsupported", "grounded", "declines_when_unsupported"),
]


def render_table(runs: list[dict[str, Any]]) -> str:
    """Markdown comparison table for docs/MODELS.md, one row per run."""

    def percent(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.0%}"

    def number(value: float | None, unit: str = "") -> str:
        return "n/a" if value is None else f"{value:g}{unit}"

    header = ["Model", *[title for title, _, _ in COLUMNS], "Tok/s", "TTFT", "Total", "Memory"]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for run in runs:
        speed = run["throughput"]
        cells = [
            run["label"],
            *[percent(run["metrics"][task][metric]) for _, task, metric in COLUMNS],
            number(speed["median_tokens_per_s"]),
            number(speed["median_ttft_s"], " s"),
            number(speed["total_s"], " s"),
            number(run.get("memory_gb"), " GB"),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)
