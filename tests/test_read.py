from datetime import UTC, datetime
from pathlib import Path

import httpx
from typer.testing import CliRunner

from mai.cli import app
from mai.config import FetchSettings
from mai.extract import Document, ExtractError
from mai.fetch import FetchedPage, Fetcher, FetchError
from mai.read import read_many, read_url

ARTICLE = (Path(__file__).parent / "fixtures" / "article.html").read_bytes()
HTML = {"content-type": "text/html; charset=utf-8"}


def site(request: httpx.Request) -> httpx.Response:
    routes = {
        "/article": httpx.Response(200, content=ARTICLE, headers=HTML),
        "/empty": httpx.Response(200, content=b"<html><body></body></html>", headers=HTML),
        "/robots.txt": httpx.Response(404),
    }
    return routes.get(request.url.path, httpx.Response(404))


def fetcher(tmp_path) -> Fetcher:
    settings = FetchSettings(per_host_interval_s=0)
    return Fetcher(settings, tmp_path / "cache", transport=httpx.MockTransport(site))


async def test_read_url_fetches_and_extracts(tmp_path):
    async with fetcher(tmp_path) as web:
        document = await read_url(web, "https://example.com/article")
    assert document.title.startswith("Tidal power comes of age")
    assert "62 megawatts" in document.text


async def test_read_many_reports_each_kind_of_failure_in_place(tmp_path):
    async with fetcher(tmp_path) as web:
        good, empty, missing = await read_many(
            web,
            ["https://example.com/article", "https://example.com/empty", "https://example.com/no"],
        )
    assert isinstance(good, Document)
    assert isinstance(empty, ExtractError) and empty.needs_browser
    assert isinstance(missing, FetchError) and missing.kind == "http"


def test_cli_fetch_refuses_local_addresses():
    result = CliRunner().invoke(app, ["fetch", "http://localhost:8888/"])
    assert result.exit_code == 1
    assert "private or local address" in result.output


class FakeBrowser:
    """Stands in for the headless browser: returns fixed HTML and records calls."""

    def __init__(self, body: bytes = ARTICLE, error: FetchError | None = None) -> None:
        self.body = body
        self.error = error
        self.rendered: list[str] = []

    async def render(self, url: str) -> FetchedPage:
        self.rendered.append(url)
        if self.error:
            raise self.error
        return FetchedPage(
            url=url,
            final_url=url,
            status=200,
            content_type="text/html",
            charset="utf-8",
            body=self.body,
            fetched_at=datetime.now(UTC),
        )

    async def aclose(self) -> None: ...


async def test_browser_is_used_only_when_a_page_has_no_text(tmp_path):
    browser = FakeBrowser()
    async with fetcher(tmp_path) as web:
        await read_url(web, "https://example.com/article", browser)
        assert browser.rendered == []  # plain fetch was enough

        document = await read_url(web, "https://example.com/empty", browser)
        assert browser.rendered == ["https://example.com/empty"]
        assert "62 megawatts" in document.text

        again = await read_url(web, "https://example.com/empty", browser)
        assert len(browser.rendered) == 1  # the rendered page was cached
        assert again.text == document.text


async def test_browser_is_not_used_to_get_around_a_refusal(tmp_path):
    browser = FakeBrowser()
    async with fetcher(tmp_path) as web:
        missing, local = await read_many(
            web, ["https://example.com/no", "http://localhost/x"], browser
        )
    assert isinstance(missing, FetchError) and isinstance(local, FetchError)
    assert browser.rendered == []


async def test_a_render_that_is_still_empty_or_fails_is_reported_and_not_cached(tmp_path):
    async with fetcher(tmp_path) as web:
        empty = FakeBrowser(body=b"<html><body></body></html>")
        (first,) = await read_many(web, ["https://example.com/empty"], empty)
        assert isinstance(first, ExtractError)

        broken = FakeBrowser(error=FetchError("https://example.com/empty", "timeout", "Timed out"))
        (second,) = await read_many(web, ["https://example.com/empty"], broken)
        assert isinstance(second, FetchError) and second.kind == "timeout"
        assert broken.rendered == ["https://example.com/empty"]  # nothing was cached
