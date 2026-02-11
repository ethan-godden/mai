import asyncio
import json
import time
from datetime import date
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from mai.cli import app
from mai.config import SearchSettings
from mai.search import (
    SearchError,
    SearchResult,
    build_providers,
    canonical_url,
    merge_results,
    search_all,
)
from mai.search.arxiv import ArxivProvider, build_query
from mai.search.base import parse_date
from mai.search.searxng import SearxngProvider
from mai.search.semantic_scholar import SemanticScholarProvider
from mai.search.wikipedia import WikipediaProvider

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def result(url: str, provider: str = "a", **kwargs) -> SearchResult:
    return SearchResult(url=url, title=url, providers=(provider,), **kwargs)


class StubProvider:
    def __init__(self, name: str, results=None, error: Exception | None = None) -> None:
        self.name = name
        self._results = results or []
        self._error = error

    async def search(self, query: str, *, max_results: int = 10) -> list[SearchResult]:
        if self._error:
            raise self._error
        return self._results


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("https://www.example.com/page/", "http://example.com/page"),
        ("https://example.com/p?utm_source=x&id=2&fbclid=z", "https://example.com/p?id=2"),
        ("https://Example.com/p#section", "https://example.com/p"),
    ],
)
def test_canonical_url_treats_equivalent_urls_as_equal(a, b):
    assert canonical_url(a) == canonical_url(b)


def test_canonical_url_keeps_meaningful_differences():
    assert canonical_url("https://example.com/a?id=1") != canonical_url(
        "https://example.com/a?id=2"
    )
    assert canonical_url("https://example.com/A") != canonical_url("https://example.com/a")
    assert canonical_url("http://localhost:8888/x") == "https://localhost:8888/x"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2024-03-05T10:00:00", date(2024, 3, 5)),
        ("2024-03-05T10:00:00Z", date(2024, 3, 5)),
        ("2024-03-05", date(2024, 3, 5)),
        ("", None),
        (None, None),
        ("last week", None),
    ],
)
def test_parse_date(value, expected):
    assert parse_date(value) == expected


def test_merge_ranks_agreement_first_and_combines_fields():
    first = [result("https://x.com/1", "a"), result("https://x.com/shared", "a", snippet="short")]
    second = [
        result("https://www.x.com/shared/", "b", snippet="a longer snippet", engines=("bing",)),
        result("https://x.com/2", "b"),
    ]
    merged = merge_results([first, second], max_results=10)

    assert [r.url for r in merged] == ["https://x.com/shared", "https://x.com/1", "https://x.com/2"]
    assert merged[0].providers == ("a", "b")
    assert merged[0].snippet == "a longer snippet"
    assert merged[0].engines == ("bing",)
    assert len(merge_results([first, second], max_results=2)) == 2


def test_merge_ignores_duplicates_within_one_provider():
    repeated = [result("https://x.com/1"), result("https://x.com/1/"), result("https://x.com/2")]
    other = [result("https://x.com/2", "b")]
    assert merge_results([repeated, other], 10)[0].url == "https://x.com/2"


async def test_search_all_survives_a_failing_provider():
    good = StubProvider("good", [result("https://x.com/1", "good")])
    bad = StubProvider("bad", error=SearchError("bad", "HTTP 500"))
    outcome = await search_all([good, bad], "q")
    assert [r.url for r in outcome.results] == ["https://x.com/1"]
    assert outcome.errors == {"bad": "HTTP 500"}


async def test_search_all_does_not_hide_programming_errors():
    with pytest.raises(KeyError):
        await search_all([StubProvider("broken", error=KeyError("bug"))], "q")


async def test_searxng_parses_a_recorded_response():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=fixture("searxng_search.json"))

    async with client_for(handler) as client:
        results = await SearxngProvider(client, "http://sx:8888/").search(
            "heat pumps", max_results=4
        )

    assert requests[0].url.path == "/search"
    assert dict(requests[0].url.params) == {"q": "heat pumps", "format": "json"}
    assert len(results) == 4
    top = results[0]
    assert top.url.startswith("https://www.carrier.com/")
    assert top.providers == ("searxng",)
    assert top.engines == ("google cse", "duckduckgo")
    assert "heat pump" in top.snippet.lower()


async def test_searxng_explains_common_failures():
    async def search(handler):
        async with client_for(handler) as client:
            return await SearxngProvider(client, "http://sx:8888").search("q")

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(SearchError, match=r"scripts/searxng\.sh run"):
        await search(refused)
    with pytest.raises(SearchError, match=r"search\.formats"):
        await search(lambda request: httpx.Response(403))
    with pytest.raises(SearchError, match="rate limited"):
        await search(lambda request: httpx.Response(429))

    blocked = {"results": [], "unresponsive_engines": [["google", "CAPTCHA"], ["bing", "timeout"]]}
    with pytest.raises(SearchError, match=r"google \(CAPTCHA\), bing \(timeout\)"):
        await search(lambda request: httpx.Response(200, json=blocked))

    assert await search(lambda request: httpx.Response(200, json={"results": []})) == []


async def test_searxng_skips_results_that_are_not_web_pages():
    payload = {
        "results": [{"url": "magnet:?xt=1", "title": "t"}, {"url": "https://ok.com", "title": ""}]
    }
    async with client_for(lambda request: httpx.Response(200, json=payload)) as client:
        results = await SearxngProvider(client, "http://sx").search("q")
    assert [(r.url, r.title) for r in results] == [("https://ok.com", "https://ok.com")]


def test_build_providers_rejects_unknown_names():
    settings = SearchSettings()
    client = httpx.AsyncClient()
    assert [p.name for p in build_providers(client, settings)] == ["searxng"]
    assert len(build_providers(client, settings, ["searxng", "searxng"])) == 1
    with pytest.raises(ValueError, match="Unknown search provider"):
        build_providers(client, settings, ["altavista"])


def test_cli_search_reports_unknown_provider_and_unreachable_server(monkeypatch):
    runner = CliRunner()
    assert runner.invoke(app, ["search", "q", "-p", "altavista"]).exit_code == 2

    monkeypatch.setenv("MAI_SEARCH__SEARXNG_URL", "http://127.0.0.1:9")
    failed = runner.invoke(app, ["search", "q"])
    assert failed.exit_code == 1
    assert "searxng failed" in failed.output and "No results" in failed.output


def test_fixture_is_valid_json():
    assert json.loads(fixture("searxng_search.json"))["results"]


def serving(body: str, seen: list[httpx.Request] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, text=body)

    return handler


async def test_wikipedia_parses_a_recorded_response():
    seen: list[httpx.Request] = []
    async with client_for(serving(fixture("wikipedia_search.json"), seen)) as client:
        results = await WikipediaProvider(client).search("retrieval-augmented generation")

    assert seen[0].url.host == "en.wikipedia.org"
    assert seen[0].url.params["srsearch"] == "retrieval-augmented generation"
    top = results[0]
    assert top.title == "Retrieval-augmented generation"
    assert top.url == "https://en.wikipedia.org/wiki/Retrieval-augmented_generation"
    assert top.snippet.startswith("Retrieval-augmented generation (RAG) is a technique")
    assert "<" not in top.snippet  # highlight markup removed
    assert top.published is None


async def test_wikipedia_escapes_titles_and_uses_configured_language():
    payload = {"query": {"search": [{"title": "C++ (langage) é?", "snippet": "a &amp; b"}]}}
    seen: list[httpx.Request] = []
    async with client_for(serving(json.dumps(payload), seen)) as client:
        results = await WikipediaProvider(client, "fr").search("q")
    assert seen[0].url.host == "fr.wikipedia.org"
    assert results[0].url == "https://fr.wikipedia.org/wiki/C%2B%2B_(langage)_%C3%A9%3F"
    assert results[0].snippet == "a & b"


def test_arxiv_query_requires_every_meaningful_word():
    assert build_query("What is retrieval-augmented generation?") == (
        "all:retrieval-augmented AND all:generation"
    )
    assert build_query("to be or not to be") == "all:not"
    assert build_query("the of") == "all:the AND all:of"  # nothing left: keep the words


async def test_arxiv_parses_a_recorded_response():
    seen: list[httpx.Request] = []
    async with client_for(serving(fixture("arxiv_search.xml"), seen)) as client:
        results = await ArxivProvider(client, min_interval_s=0).search(
            "retrieval augmented", max_results=2
        )

    assert seen[0].url.params["search_query"] == "all:retrieval AND all:augmented"
    assert seen[0].url.params["max_results"] == "2"
    assert len(results) == 2
    top = results[0]
    assert top.url == "https://arxiv.org/abs/2506.06962"  # https, version removed
    assert top.title == "AR-RAG: Autoregressive Retrieval Augmentation for Image Generation"
    assert top.snippet.startswith("We introduce Autoregressive Retrieval Augmentation")
    assert "\n" not in top.snippet
    assert top.published == date(2025, 6, 8)


async def test_arxiv_spaces_out_requests_and_rejects_bad_xml():
    async with client_for(serving(fixture("arxiv_search.xml"))) as client:
        provider = ArxivProvider(client, min_interval_s=0.05)
        started = time.monotonic()
        await asyncio.gather(provider.search("a b"), provider.search("c d"))
        assert time.monotonic() - started >= 0.05

    async with client_for(serving("<feed><unclosed>")) as client:
        with pytest.raises(SearchError, match="not valid XML"):
            await ArxivProvider(client, min_interval_s=0).search("q")


async def test_semantic_scholar_parses_the_documented_response_shape():
    """The fixture is hand-written from the API documentation, not recorded:
    every unauthenticated request made while building this returned HTTP 429."""
    seen: list[httpx.Request] = []
    async with client_for(serving(fixture("semantic_scholar_search.json"), seen)) as client:
        results = await SemanticScholarProvider(client, api_key="k").search("rag", max_results=5)

    assert seen[0].headers["x-api-key"] == "k"
    assert seen[0].url.params["limit"] == "5"
    assert [r.url for r in results] == [
        "https://arxiv.org/abs/2005.11401",
        "https://doi.org/10.1000/example.doi",
        "https://www.semanticscholar.org/paper/0000000000000000000000000000000000000003",
    ]
    assert [r.published for r in results] == [date(2020, 5, 22), date(2023, 1, 1), None]
    assert results[1].snippet == ""


async def test_semantic_scholar_sends_no_key_header_by_default_and_reports_rate_limits():
    seen: list[httpx.Request] = []
    async with client_for(serving('{"data": null}', seen)) as client:
        assert await SemanticScholarProvider(client).search("q") == []
    assert "x-api-key" not in seen[0].headers

    async with client_for(lambda request: httpx.Response(429)) as client:
        with pytest.raises(SearchError, match="rate limited"):
            await SemanticScholarProvider(client).search("q")


async def test_the_same_paper_from_two_providers_merges():
    async with client_for(serving(fixture("semantic_scholar_search.json"))) as client:
        scholar = await SemanticScholarProvider(client).search("q")
    arxiv = [result("http://arxiv.org/abs/2005.11401", "arxiv")]
    merged = merge_results([arxiv, scholar], 10)
    assert merged[0].providers == ("arxiv", "semantic_scholar")
