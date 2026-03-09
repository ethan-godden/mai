from datetime import date

import pytest

from mai import prompts
from mai.rewrite import SearchQueries, rewrite_question
from tests.fakes import FakeLLM


async def test_rewrite_uses_the_fast_tier_and_includes_question_and_date():
    llm = FakeLLM({SearchQueries: SearchQueries(queries=["heat pump cold COP", "heat pump cost"])})
    queries = await rewrite_question(llm, "Are heat pumps worth it?", today=date(2026, 11, 23))

    assert queries == ["heat pump cold COP", "heat pump cost"]
    kind, tier, messages = llm.calls[0]
    assert (kind, tier) == ("structured", "fast")
    assert "Are heat pumps worth it?" in messages[0]["content"]
    assert "2026-11-23" in messages[0]["content"]


async def test_rewrite_removes_duplicates_blanks_and_extras():
    raw = ["Heat  pumps", "heat pumps", " ", "a", "b", "c", "d"]
    llm = FakeLLM({SearchQueries: SearchQueries(queries=raw[:6])})
    assert await rewrite_question(llm, "q") == ["Heat pumps", "a", "b", "c"]


async def test_rewrite_falls_back_to_the_question():
    assert await rewrite_question(FakeLLM(), "What is RAG?") == ["What is RAG?"]
    blank = FakeLLM({SearchQueries: SearchQueries(queries=["  "])})
    assert await rewrite_question(blank, "What is RAG?") == ["What is RAG?"]


def test_prompts_render_and_reject_missing_values():
    text = prompts.render("answer_user", sources="[1] A", question="Why {braces}?")
    assert text == "Sources:\n\n[1] A\n\nQuestion: Why {braces}?"
    with pytest.raises(KeyError):
        prompts.render("rewrite", question="q")
