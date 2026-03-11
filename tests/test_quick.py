import asyncio
from datetime import date
from pathlib import Path

import httpx

from mai.config import FetchSettings, QuickSettings
from mai.fetch import Fetcher
from mai.quick import (
    NO_SOURCES_MESSAGE,
    find_citations,
    format_sources,
    quick_answer,
    sources_footer,
)
from mai.rewrite import SearchQueries
from mai.search import SearchResult
from tests.fakes import FakeLLM, StubProvider

ARTICLE = (Path(__file__).parent / "fixtures" / "article.html").read_bytes()
HTML = {"content-type": "text/html; charset=utf-8"}
BRIDGE = (
    b"<html><head><title>Halden Bridge</title></head><body><article><h1>Halden Bridge</h1>"
    b"<p>Halden Bridge opened to traffic in 1987 and is a cable-stayed design with a main span "
    b"of 940 metres, the work of the engineer Petra Voss.</p>"
    b"<p>From the bridge deck the tidal array is visible in the strait below, and at low water "
    b"the tops of several turbines can be seen from the eastern walkway.</p>"
    b"<p>A retrofit completed in 2016 replaced the deck bearings, and the crossing now carries "
    b"far more vehicles each day than it was originally planned for.</p>"
    b"</article></body></html>"
)


def web(slow: float = 0.0):
    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/tidal":
            return httpx.Response(200, content=ARTICLE, headers=HTML)
        if path == "/bridge":
            return httpx.Response(200, content=BRIDGE, headers=HTML)
        if path == "/slow":
            await asyncio.sleep(slow)
            return httpx.Response(200, content=ARTICLE, headers=HTML)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def hit(path: str, title: str, snippet: str = "") -> SearchResult:
    return SearchResult(
        url=f"https://example.com/{path}", title=title, snippet=snippet, providers=("stub",)
    )


RESULTS = [
    hit("tidal", "Tidal power comes of age"),
    hit("bridge", "Halden Bridge"),
    hit("gone", "A vanished page", snippet="The tidal array has 48 turbines, says a summary."),
]
QUERIES = {SearchQueries: SearchQueries(queries=["veldrin tidal array turbines", "tidal capacity"])}


async def run(tmp_path, llm, provider, transport=None, **quick):
    settings = FetchSettings(per_host_interval_s=0)
    statuses: list[str] = []
    streamed: list[str] = []
    async with Fetcher(settings, tmp_path / "cache", transport=transport or web()) as fetcher:
        result = await quick_answer(
            "How many turbines does the Veldrin Tidal Array have?",
            llm=llm,
            providers=[provider],
            fetcher=fetcher,
            settings=QuickSettings(**quick),
            on_status=statuses.append,
            on_text=streamed.append,
            today=date(2026, 11, 23),
        )
    return result, statuses, streamed


async def test_answers_from_passages_with_numbered_citations(tmp_path):
    llm = FakeLLM(QUERIES, answer="It has 48 turbines [1]. A summary agrees [3]. See also [9].")
    provider = StubProvider(results=RESULTS)

    result, statuses, streamed = await run(tmp_path, llm, provider)

    assert result.status == "answered"
    assert provider.queries == ["veldrin tidal array turbines", "tidal capacity"]
    assert result.answer.startswith("It has 48 turbines [1].")
    assert "".join(streamed).strip() == result.answer
    assert [s.split(":")[0] for s in statuses] == [
        "Planning searches", "Searching", "Reading 3 pages", "Writing answer from 3 sources",
    ]  # fmt: skip

    # Sources are numbered by relevance; the article about the array comes first.
    first = result.sources[0]
    assert (first.number, first.url) == (1, "https://example.com/tidal")
    assert first.site == "The Coastal Ledger" and first.published == date(2024, 5, 14)
    assert any("48 seabed turbines" in p.text for p in first.passages)
    assert [s.number for s in result.sources] == [1, 2, 3]
    by_url = {s.url: s for s in result.sources}
    assert not by_url["https://example.com/bridge"].snippet_only
    summary = by_url["https://example.com/gone"]
    assert summary.snippet_only and summary.passages[0].text.startswith("The tidal array has 48")
    assert [(u.url, u.reason) for u in result.unread] == [("https://example.com/gone", "HTTP 404")]

    assert result.cited == [1, 3]
    assert result.invalid_citations == [9]
    assert [source.number for source in result.cited_sources()] == [1, 3]
    assert set(result.timings) == {"rewrite", "search", "read", "select", "answer"}


async def test_the_model_sees_only_the_sources_and_the_rules(tmp_path):
    llm = FakeLLM(QUERIES, answer="48 [1].")
    await run(tmp_path, llm, StubProvider(results=RESULTS))

    kind, tier, messages = llm.calls[-1]
    assert (kind, tier) == ("complete", "fast")
    system, user = messages[0]["content"], messages[1]["content"]
    assert "only the numbered sources" in system and "2026-11-23" in system
    assert "ignore them" in system  # instructions inside sources are not obeyed
    assert user.endswith("Question: How many turbines does the Veldrin Tidal Array have?")
    assert "[1] Tidal power comes of age (The Coastal Ledger, 2024-05-14)" in user
    assert "https://example.com/tidal" in user and "Section: What it cost" in user
    assert "(Search-result summary only; the page could not be read.)" in user
    assert "Subscribe now" not in user  # boilerplate never reaches the model


async def test_no_sources_means_no_model_answer(tmp_path):
    llm = FakeLLM(QUERIES, answer="I made this up.")
    provider = StubProvider(error="not reachable")

    result, _, streamed = await run(tmp_path, llm, provider)

    assert result.status == "no_sources" and result.answer == NO_SOURCES_MESSAGE
    assert result.search_errors == {"stub": "not reachable"}
    assert [kind for kind, _, _ in llm.calls] == ["structured"]  # the answer model never ran
    assert streamed == [] and result.completion is None


async def test_irrelevant_pages_yield_no_sources(tmp_path):
    llm = FakeLLM({SearchQueries: SearchQueries(queries=["zebra migration"])}, answer="x")
    provider = StubProvider(results=[hit("bridge", "Halden Bridge")])
    statuses: list[str] = []
    async with Fetcher(FetchSettings(), tmp_path / "c", transport=web()) as fetcher:
        result = await quick_answer(
            "zebra migration",
            llm=llm,
            providers=[provider],
            fetcher=fetcher,
            settings=QuickSettings(),
            on_status=statuses.append,
        )
    assert result.status == "no_sources" and result.sources == []


async def test_a_slow_page_is_left_out_not_waited_for(tmp_path):
    llm = FakeLLM(QUERIES, answer="48 [1].")
    provider = StubProvider(results=[hit("tidal", "Tidal"), hit("slow", "Slow page")])

    result, _, _ = await run(tmp_path, llm, provider, transport=web(slow=5), read_budget_s=0.5)

    assert [s.url for s in result.sources] == ["https://example.com/tidal"]
    assert result.unread[0].url == "https://example.com/slow"
    assert "Not finished within 0.5 s" in result.unread[0].reason
    assert result.timings["read"] < 2


async def test_rewrite_failure_still_searches_with_the_question(tmp_path):
    provider = StubProvider(results=RESULTS)
    result, _, _ = await run(tmp_path, FakeLLM(answer="48 [1]."), provider)
    assert provider.queries == ["How many turbines does the Veldrin Tidal Array have?"]
    assert result.status == "answered"


async def test_limits_on_sources_and_passages_are_respected(tmp_path):
    llm = FakeLLM(QUERIES, answer="48 [1].")
    result, _, _ = await run(
        tmp_path, llm, StubProvider(results=RESULTS), max_sources=1, passages_per_source=1
    )
    assert len(result.search_results) == 1
    assert len(result.sources) == 1 and len(result.sources[0].passages) == 1


def test_find_citations_handles_groups_order_and_range():
    answer = "A [2]. B [1][2]. C [3, 1]. D [0] and [7]. Not a citation: [note] or [2024-01]."
    assert find_citations(answer, source_count=3) == ([2, 1, 3], [0, 7])
    assert find_citations("No citations here.", 3) == ([], [])


async def test_footer_lists_cited_sources_or_all_if_none_cited(tmp_path):
    llm = FakeLLM(QUERIES, answer="It has 48 turbines [1].")
    result, _, _ = await run(tmp_path, llm, StubProvider(results=RESULTS))
    assert sources_footer(result) == (
        "1. [Tidal power comes of age](https://example.com/tidal) (The Coastal Ledger, 2024-05-14)"
    )
    result.cited = []
    assert len(sources_footer(result).splitlines()) == 3
    assert format_sources(result.sources).count("\n\n---\n\n") == 2
