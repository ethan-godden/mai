from pathlib import Path

import httpx
from typer.testing import CliRunner

from mai.cli import app
from mai.config import FetchSettings
from mai.extract import Document, ExtractError
from mai.fetch import Fetcher, FetchError
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
