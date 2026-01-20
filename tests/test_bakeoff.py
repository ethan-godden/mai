from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from mai.bakeoff import (
    SCHEMAS,
    BakeoffData,
    GroundedAnswer,
    Note,
    NoteList,
    SearchPlan,
    has_all_keywords,
    has_keyword,
    is_verbatim,
    render_table,
    run_bakeoff,
)
from mai.llm import Completion, StructuredOutputError, StructuredResult

DATA_DIR = Path(__file__).parent.parent / "evals" / "bakeoff"


@pytest.mark.parametrize(
    ("text", "keyword", "expected"),
    [
        ("It has 24 cores", "24", True),
        ("Released in November 2024", "24", False),  # part of a longer number
        ("Rated 1.24 watts", "24", False),
        ("enrolled 2,840 people", "2840", True),
        ("48 turbines", "turbine", True),
        ("Two round trips", "2 round|two round", True),
        ("designed by petra VOSS", "Voss", True),
        ("nothing relevant", "Voss", False),
    ],
)
def test_has_keyword(text, keyword, expected):
    assert has_keyword(text, keyword) is expected


def test_has_all_keywords_requires_every_one():
    assert has_all_keywords("Voss designed the 940 metre span", ["Voss", "940"])
    assert not has_all_keywords("Voss designed it", ["Voss", "940"])


def test_is_verbatim_ignores_case_spacing_and_edge_punctuation():
    passage = 'The canal is 82 kilometres long. "We doubled our funding," she said.'
    assert is_verbatim("the canal is  82 kilometres long.", passage)
    assert is_verbatim("“We doubled our funding,” she said", passage)
    assert not is_verbatim("The canal is 83 kilometres long", passage)
    assert not is_verbatim("", passage)


def test_shipped_data_is_consistent():
    data = BakeoffData.load(DATA_DIR)
    assert all(case["schema"] in SCHEMAS for case in data.structured)
    for case in data.grounded:
        assert set(case["passages"]) <= set(data.passages)
        if case["gold"] is None:
            assert not case["keywords"]
        else:
            # The gold passage is supplied, and it really contains the answer.
            assert case["gold"] in case["passages"]
            assert has_all_keywords(data.passages[case["gold"]]["text"], case["keywords"])
    for passage in data.passages.values():
        for fact in passage["facts"]:
            assert has_all_keywords(passage["text"], fact["keywords"]), fact


def test_unanswerable_questions_are_truly_unsupported():
    """No supplied passage may contain the answer to a question marked unanswerable."""
    data = BakeoffData.load(DATA_DIR)
    answers = {"g9": "financial officer", "g10": "2840", "g11": "price", "g12": "banned in"}
    unanswerable = [case for case in data.grounded if case["gold"] is None]
    assert {case["id"] for case in unanswerable} == set(answers)
    for case in unanswerable:
        supplied = " ".join(data.passages[pid]["text"] for pid in case["passages"])
        assert not has_keyword(supplied, answers[case["id"]])


class ScriptedLLM:
    """Fake model: answers by schema, optionally failing on chosen prompts."""

    def __init__(self, answers: dict[type[BaseModel], Any], fail_on: str = "") -> None:
        self.answers = answers
        self.fail_on = fail_on

    async def complete(self, *args, **kwargs) -> Completion:
        raise NotImplementedError

    async def structured(self, tier, messages, schema, **kwargs):
        prompt = messages[-1]["content"]
        if self.fail_on and self.fail_on in prompt:
            raise StructuredOutputError(schema.__name__, "junk", "invalid")
        answer = self.answers.get(schema)
        value: Any = answer(prompt) if callable(answer) else answer
        if value is None:
            value = schema.model_construct()
        completion = Completion(
            text="", tier=tier, model="fake", finish_reason="stop",
            prompt_tokens=10, completion_tokens=20, duration_s=1.5, ttft_s=0.5,
        )  # fmt: skip
        return StructuredResult(value=value, completion=completion, attempts=1, constrained=True)


def perfect_grounded(data: BakeoffData):
    def answer(prompt: str) -> GroundedAnswer:
        case = next(c for c in data.grounded if c["question"] in prompt)
        if case["gold"] is None:
            return GroundedAnswer(answerable=False, answer="", citations=[])
        text = data.passages[case["gold"]]["text"]
        return GroundedAnswer(answerable=True, answer=text, citations=[case["gold"]])

    return answer


def perfect_notes(data: BakeoffData):
    def answer(prompt: str) -> NoteList:
        passage = next((p for p in data.passages.values() if p["text"] in prompt), None)
        if passage is None:  # a structured-output case, scored on validity only
            return NoteList(notes=[Note(claim="c", quote="q")])
        sentences = [s.strip() + "." for s in passage["text"].split(". ") if s.strip()]
        return NoteList(notes=[Note(claim=s, quote=s) for s in sentences])

    return answer


async def test_perfect_model_scores_full_marks():
    data = BakeoffData.load(DATA_DIR)
    llm = ScriptedLLM(
        {
            SearchPlan: SearchPlan(queries=["a", "b"]),
            GroundedAnswer: perfect_grounded(data),
            NoteList: perfect_notes(data),
        }
    )
    run = await run_bakeoff(llm, data)

    for task, metrics in run["metrics"].items():
        assert all(value == 1.0 for value in metrics.values()), (task, metrics)
    speed = run["throughput"]
    assert speed["calls"] == sum(
        len(x) for x in (data.structured, data.rewrite, data.passages, data.grounded)
    )
    assert speed["failed_calls"] == 0 and speed["schema_enforced"] == 1.0
    assert speed["median_tokens_per_s"] == 20.0  # 20 tokens over (1.5 - 0.5) s
    assert len(run["samples"]["grounded"]) == len(data.grounded)


async def test_wrong_answers_and_failures_lower_the_scores():
    data = BakeoffData.load(DATA_DIR)

    def careless(prompt: str) -> GroundedAnswer:
        # Always answers, cites everything, never declines.
        return GroundedAnswer(answerable=True, answer="unknown", citations=["p1", "p2"])

    llm = ScriptedLLM(
        {
            SearchPlan: SearchPlan(queries=["same", "same"]),
            GroundedAnswer: careless,
            NoteList: NoteList(notes=[Note(claim="Something", quote="not in the passage")]),
        },
        fail_on="nuclear fusion",
    )
    run = await run_bakeoff(llm, data)
    metrics = run["metrics"]

    assert metrics["structured"]["valid_after_repair"] == round(11 / 12, 3)
    assert metrics["rewrite"] == {"valid": 1.0, "distinct": 0.0}
    assert metrics["notes"] == {"fact_recall": 0.0, "quote_verbatim": 0.0}
    assert metrics["grounded"] == {
        "answer_correct": 0.0,
        "citation_correct": 0.0,
        "declines_when_unsupported": 0.0,
    }
    assert run["throughput"]["failed_calls"] == 1


def test_render_table_handles_missing_values():
    run = {
        "label": "m",
        "metrics": {
            "structured": {"valid_first_try": 1.0, "valid_after_repair": 1.0},
            "notes": {"fact_recall": 0.5, "quote_verbatim": None},
            "grounded": {
                "answer_correct": 0.875,
                "citation_correct": 0.75,
                "declines_when_unsupported": 1.0,
            },
        },
        "throughput": {"median_tokens_per_s": 41.5, "median_ttft_s": None, "total_s": 90.0},
    }
    table = render_table([run]).splitlines()
    assert len(table) == 3
    assert (
        table[2] == "| m | 100% | 100% | 50% | n/a | 88% | 75% | 100% | 41.5 | n/a | 90 s | n/a |"
    )
