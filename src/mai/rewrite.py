"""Turn a research question into search queries."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from mai import prompts
from mai.llm import LLM, StructuredOutputError

MAX_QUERIES = 4


class SearchQueries(BaseModel):
    queries: list[str] = Field(min_length=1, max_length=6)


async def rewrite_question(llm: LLM, question: str, *, today: date | None = None) -> list[str]:
    """Ask the fast model for 2 to 4 search queries.

    Falls back to the question itself if the model cannot produce valid
    output: a plain search is better than no answer.
    """
    prompt = prompts.render("rewrite", question=question, today=today or date.today())
    try:
        result = await llm.structured("fast", [{"role": "user", "content": prompt}], SearchQueries)
    except StructuredOutputError:
        return [question]
    # Models sometimes repeat a query with different capitalisation or spacing.
    unique: dict[str, str] = {}
    for query in result.value.queries:
        cleaned = " ".join(query.split())
        if cleaned:
            unique.setdefault(cleaned.lower(), cleaned)
    return list(unique.values())[:MAX_QUERIES] or [question]
