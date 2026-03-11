"""Quick mode: one search pass, then a short answer with numbered citations.

    question -> search queries -> search -> read pages -> pick passages -> answer

Each stage is an ordinary function call, in order, with a bounded amount of
work. The model is used twice: the fast tier writes the search queries, and
the answer tier writes the answer from the selected passages only.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from mai import prompts
from mai.config import QuickSettings
from mai.extract import Document, ExtractError
from mai.fetch import Fetcher, FetchError
from mai.llm import LLM, Completion, strip_reasoning
from mai.passages import Passage, chunk_document, select_passages
from mai.read import read_url
from mai.render import Renderer
from mai.rewrite import rewrite_question
from mai.search import SearchProvider, SearchResult, merge_results, search_all

StatusCallback = Callable[[str], None]
TextCallback = Callable[[str], None]

_CITATION = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")

NO_SOURCES_MESSAGE = (
    "I could not find any readable sources for this question, so I have not answered it. "
    "Check that the search service is running (mai doctor) or try rephrasing."
)


@dataclass(frozen=True)
class Source:
    number: int
    """The citation number used in the answer, from 1."""
    url: str
    title: str
    site: str | None
    published: date | None
    passages: list[Passage]
    snippet_only: bool = False
    """True if the page could not be read and only the search snippet was used."""


@dataclass(frozen=True)
class Unread:
    url: str
    reason: str


@dataclass
class QuickResult:
    question: str
    status: Literal["answered", "no_sources"]
    answer: str
    queries: list[str] = field(default_factory=list)
    search_results: list[SearchResult] = field(default_factory=list)
    search_errors: dict[str, str] = field(default_factory=dict)
    sources: list[Source] = field(default_factory=list)
    unread: list[Unread] = field(default_factory=list)
    cited: list[int] = field(default_factory=list)
    """Source numbers the answer cites, in order of first use."""
    invalid_citations: list[int] = field(default_factory=list)
    """Numbers the answer cites that match no source."""
    timings: dict[str, float] = field(default_factory=dict)
    completion: Completion | None = None

    def cited_sources(self) -> list[Source]:
        return [source for source in self.sources if source.number in self.cited]


def find_citations(answer: str, source_count: int) -> tuple[list[int], list[int]]:
    """Return (valid, invalid) citation numbers found in ``answer``, each in order of first use."""
    valid: list[int] = []
    invalid: list[int] = []
    for match in _CITATION.finditer(answer):
        for number in (int(part) for part in match.group(1).split(",")):
            bucket = valid if 1 <= number <= source_count else invalid
            if number not in bucket:
                bucket.append(number)
    return valid, invalid


def format_sources(sources: Sequence[Source]) -> str:
    """Lay the sources out for the answer prompt."""
    blocks = []
    for source in sources:
        details = ", ".join(str(part) for part in (source.site, source.published) if part)
        header = f"[{source.number}] {source.title}" + (f" ({details})" if details else "")
        body = "\n...\n".join(
            (f"Section: {passage.heading}\n" if passage.heading else "") + passage.text
            for passage in source.passages
        )
        note = (
            "(Search-result summary only; the page could not be read.)\n"
            if source.snippet_only
            else ""
        )
        blocks.append(f"{header}\n{source.url}\n{note}{body}")
    return "\n\n---\n\n".join(blocks)


def sources_footer(result: QuickResult) -> str:
    """Markdown list of the sources the answer cites (all of them if it cited none)."""
    listed = result.cited_sources() or result.sources
    lines = []
    for source in listed:
        details = ", ".join(str(part) for part in (source.site, source.published) if part)
        suffix = f" ({details})" if details else ""
        lines.append(f"{source.number}. [{source.title}]({source.url}){suffix}")
    return "\n".join(lines)


async def _search(
    providers: Sequence[SearchProvider], queries: Sequence[str], settings: QuickSettings
) -> tuple[list[SearchResult], dict[str, str]]:
    outcomes = await asyncio.gather(
        *(search_all(providers, query, max_results=settings.results_per_query) for query in queries)
    )
    errors: dict[str, str] = {}
    for outcome in outcomes:
        errors.update(outcome.errors)
    # Fuse across queries the same way results are fused across providers:
    # a page several queries find is more likely to be central to the question.
    merged = merge_results([outcome.results for outcome in outcomes], settings.max_sources)
    return merged, errors


async def _read(
    fetcher: Fetcher, renderer: Renderer | None, urls: Sequence[str], budget_s: float
) -> list[Document | str]:
    """Read pages concurrently within a time budget. A string is the reason a page was not read."""

    async def one(url: str) -> Document | str:
        try:
            return await read_url(fetcher, url, renderer)
        except (FetchError, ExtractError) as error:
            return error.message

    if not urls:
        return []
    tasks = [asyncio.create_task(one(url)) for url in urls]
    _, pending = await asyncio.wait(tasks, timeout=budget_s)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    return [
        task.result() if task not in pending else f"Not finished within {budget_s:g} s"
        for task in tasks
    ]


def _build_sources(
    results: Sequence[SearchResult],
    pages: Sequence[Document | str],
    query: str,
    settings: QuickSettings,
) -> tuple[list[Source], list[Unread]]:
    passages: list[Passage] = []
    unread: list[Unread] = []
    for index, (result, page) in enumerate(zip(results, pages, strict=True)):
        if isinstance(page, Document):
            passages += chunk_document(page, index, max_words=settings.passage_words)
        else:
            unread.append(Unread(result.url, page))
            if result.snippet:  # better a summary than nothing, and it is labelled as such
                passages.append(Passage(index, 0, result.snippet))

    chosen = select_passages(
        passages,
        query,
        max_passages=settings.max_passages,
        per_source=settings.passages_per_source,
    )
    by_source: dict[int, list[Passage]] = {}
    for passage in chosen:  # best first, so sources are numbered by their best passage
        by_source.setdefault(passage.source, []).append(passage)

    sources = []
    for number, (index, picked) in enumerate(by_source.items(), start=1):
        result, page = results[index], pages[index]
        document = page if isinstance(page, Document) else None
        sources.append(
            Source(
                number=number,
                url=document.url if document else result.url,
                title=result.title or (document.title if document else result.url),
                site=document.site if document else None,
                published=(document.published if document else None) or result.published,
                passages=sorted(picked, key=lambda passage: passage.position),
                snippet_only=document is None,
            )
        )
    return sources, unread


async def quick_answer(
    question: str,
    *,
    llm: LLM,
    providers: Sequence[SearchProvider],
    fetcher: Fetcher,
    settings: QuickSettings,
    renderer: Renderer | None = None,
    on_status: StatusCallback | None = None,
    on_text: TextCallback | None = None,
    today: date | None = None,
) -> QuickResult:
    """Answer ``question`` from the web with citations.

    ``on_status`` receives one-line progress messages; ``on_text`` receives the
    answer as it is generated. If no readable source is found the model is not
    asked to answer at all, so it cannot fall back on its own memory.
    """
    today = today or date.today()
    status = on_status or (lambda message: None)
    timings: dict[str, float] = {}
    started = time.perf_counter()

    def lap(stage: str) -> None:
        nonlocal started
        now = time.perf_counter()
        timings[stage] = round(now - started, 2)
        started = now

    status("Planning searches")
    queries = await rewrite_question(llm, question, today=today)
    lap("rewrite")

    status("Searching: " + " | ".join(queries))
    results, search_errors = await _search(providers, queries, settings)
    lap("search")

    status(f"Reading {len(results)} pages")
    pages = await _read(fetcher, renderer, [r.url for r in results], settings.read_budget_s)
    lap("read")

    sources, unread = _build_sources(results, pages, " ".join([question, *queries]), settings)
    lap("select")

    result = QuickResult(
        question=question,
        status="no_sources",
        answer=NO_SOURCES_MESSAGE,
        queries=queries,
        search_results=list(results),
        search_errors=search_errors,
        sources=sources,
        unread=unread,
        timings=timings,
    )
    if not sources:
        return result

    status(f"Writing answer from {len(sources)} sources")
    completion = await llm.complete(
        settings.answer_tier,
        [
            {"role": "system", "content": prompts.render("answer_system", today=today)},
            {
                "role": "user",
                "content": prompts.render(
                    "answer_user", sources=format_sources(sources), question=question
                ),
            },
        ],
        max_tokens=settings.answer_max_tokens,
        on_text=on_text,
    )
    lap("answer")

    result.status = "answered"
    result.answer = strip_reasoning(completion.text)
    result.completion = completion
    result.cited, result.invalid_citations = find_citations(result.answer, len(sources))
    return result
