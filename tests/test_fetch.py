from datetime import UTC, datetime, timedelta

import httpx
import pytest

from mai.config import FetchSettings
from mai.fetch import FetchedPage, Fetcher, FetchError, check_url

HTML = {"content-type": "text/html; charset=utf-8"}


class Site:
    """Fake web: maps URL paths to responses and records what was requested."""

    def __init__(self, routes: dict[str, httpx.Response | Exception], robots: str | int = 404):
        self.routes = routes
        self.robots = robots
        self.requests: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        if request.url.path == "/robots.txt":
            if isinstance(self.robots, int):
                return httpx.Response(self.robots)
            return httpx.Response(200, text=self.robots)
        response = self.routes[request.url.path]
        if isinstance(response, Exception):
            raise response
        return response

    def page_requests(self) -> list[str]:
        return [url for url in self.requests if not url.endswith("/robots.txt")]


class Clock:
    def __init__(self) -> None:
        self.monotonic = 0.0
        self.wall = datetime(2026, 1, 1, tzinfo=UTC)
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.monotonic += seconds


def page(text: str = "<html>hi</html>", **headers) -> httpx.Response:
    return httpx.Response(200, content=text.encode(), headers={**HTML, **headers})


def fetcher(site: Site, tmp_path, clock: Clock | None = None, **settings) -> Fetcher:
    clock = clock or Clock()
    return Fetcher(
        FetchSettings(**settings),
        tmp_path / "cache",
        transport=httpx.MockTransport(site),
        sleep=clock.sleep,
        monotonic=lambda: clock.monotonic,
        now=lambda: clock.wall,
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/x",
        "http://127.0.0.1:11434/api",
        "http://192.168.1.10/",
        "http://10.0.0.5/",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/",
        "http://printer.local/",
        "http://intranet/",
    ],
)
def test_private_and_local_addresses_are_blocked(url):
    with pytest.raises(FetchError) as info:
        check_url(url)
    assert info.value.kind == "blocked_address"
    check_url(url, allow_private=True)  # explicit opt-in


@pytest.mark.parametrize("url", ["ftp://example.com/x", "file:///etc/passwd", "example.com", ""])
def test_non_http_urls_are_invalid(url):
    with pytest.raises(FetchError) as info:
        check_url(url)
    assert info.value.kind == "invalid_url"


def test_public_addresses_pass():
    check_url("https://example.com/a?b=1")
    check_url("http://93.184.216.34/")


async def test_fetch_returns_page_and_then_serves_it_from_cache(tmp_path):
    site = Site({"/a": page("<html>hello</html>")})
    async with fetcher(site, tmp_path) as web:
        first = await web.fetch("https://example.com/a#section")
        second = await web.fetch("https://example.com/a")

    assert first.body == b"<html>hello</html>"
    assert (first.content_type, first.charset, first.status) == ("text/html", "utf-8", 200)
    assert first.url == first.final_url == "https://example.com/a"  # fragment dropped
    assert not first.from_cache and second.from_cache
    assert second.body == first.body and second.fetched_at == first.fetched_at
    assert site.page_requests() == ["https://example.com/a"]


async def test_cache_expires_and_can_be_disabled(tmp_path):
    site = Site({"/a": page()})
    clock = Clock()
    async with fetcher(site, tmp_path, clock, cache_ttl_hours=1) as web:
        await web.fetch("https://example.com/a")
        clock.wall += timedelta(hours=2)
        assert not (await web.fetch("https://example.com/a")).from_cache
    assert len(site.page_requests()) == 2

    site = Site({"/a": page()})
    async with fetcher(site, tmp_path / "off", cache_ttl_hours=0) as web:
        await web.fetch("https://example.com/a")
        assert not (await web.fetch("https://example.com/a")).from_cache


async def test_a_corrupt_cache_entry_is_refetched(tmp_path):
    site = Site({"/a": page()})
    async with fetcher(site, tmp_path) as web:
        await web.fetch("https://example.com/a")
        for meta in (tmp_path / "cache").glob("*.json"):
            meta.write_text("{not json")
        assert not (await web.fetch("https://example.com/a")).from_cache


async def test_robots_txt_is_respected_and_fetched_once_per_site(tmp_path):
    site = Site(
        {"/private/x": page(), "/public": page(), "/other": page()},
        robots="User-agent: *\nDisallow: /private/\n",
    )
    async with fetcher(site, tmp_path) as web:
        with pytest.raises(FetchError) as info:
            await web.fetch("https://example.com/private/x")
        assert info.value.kind == "robots"
        await web.fetch("https://example.com/public")
        await web.fetch("https://example.com/other")
    assert site.requests.count("https://example.com/robots.txt") == 1
    assert "https://example.com/private/x" not in site.requests


async def test_robots_rules_naming_mai_apply(tmp_path):
    robots = "User-agent: Mai\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
    async with fetcher(Site({"/a": page()}, robots=robots), tmp_path) as web:
        with pytest.raises(FetchError):
            await web.fetch("https://example.com/a")


async def test_missing_robots_allows_and_server_error_blocks(tmp_path):
    async with fetcher(Site({"/a": page()}, robots=404), tmp_path / "1") as web:
        await web.fetch("https://example.com/a")
    async with fetcher(Site({"/a": page()}, robots=503), tmp_path / "2") as web:
        with pytest.raises(FetchError) as info:
            await web.fetch("https://example.com/a")
        assert info.value.kind == "robots"
    site = Site({"/a": page()}, robots="User-agent: *\nDisallow: /\n")
    async with fetcher(site, tmp_path / "3", respect_robots=False) as web:
        await web.fetch("https://example.com/a")
    assert site.requests == ["https://example.com/a"]


async def test_failures_are_classified(tmp_path):
    site = Site(
        {
            "/missing": httpx.Response(404),
            "/image": httpx.Response(200, content=b"x", headers={"content-type": "image/png"}),
            "/slow": httpx.ReadTimeout("slow"),
            "/down": httpx.ConnectError("down"),
            "/declared-big": page(**{"content-length": "999999"}),
            "/big": page("x" * 5000),
        }
    )
    expected = {
        "/missing": "http",
        "/image": "type",
        "/slow": "timeout",
        "/down": "network",
        "/declared-big": "too_large",
        "/big": "too_large",
    }
    async with fetcher(site, tmp_path, max_bytes=1000, per_host_interval_s=0) as web:
        for path, kind in expected.items():
            with pytest.raises(FetchError) as info:
                await web.fetch(f"https://example.com{path}")
            assert info.value.kind == kind, path
    assert not list((tmp_path / "cache").glob("*"))  # failures are never cached


async def test_redirect_to_a_private_address_is_refused(tmp_path):
    site = Site({"/go": httpx.Response(302, headers={"location": "http://127.0.0.1:8888/admin"})})
    async with fetcher(site, tmp_path) as web:
        with pytest.raises(FetchError) as info:
            await web.fetch("https://example.com/go")
    assert info.value.kind == "blocked_address"
    assert not any("127.0.0.1" in url for url in site.requests)


async def test_redirects_are_followed_and_recorded(tmp_path):
    site = Site(
        {
            "/old": httpx.Response(301, headers={"location": "https://example.com/new"}),
            "/new": page(),
        }
    )
    async with fetcher(site, tmp_path, per_host_interval_s=0) as web:
        result = await web.fetch("https://example.com/old")
    assert (result.url, result.final_url) == ("https://example.com/old", "https://example.com/new")


async def test_requests_to_one_host_are_spaced_out(tmp_path):
    site = Site({"/a": page(), "/b": page(), "/c": page()})
    clock = Clock()
    async with fetcher(site, tmp_path, clock, per_host_interval_s=2.0) as web:
        results = await web.fetch_many([f"https://example.com/{p}" for p in "abc"])
        assert all(isinstance(r, FetchedPage) for r in results)
        assert clock.sleeps == [2.0, 2.0]
        clock.sleeps.clear()
        await web.fetch("https://other.org/a")  # a different host does not wait
        assert clock.sleeps == []


async def test_fetch_many_returns_failures_in_place(tmp_path):
    site = Site({"/ok": page(), "/gone": httpx.Response(410)})
    async with fetcher(site, tmp_path, per_host_interval_s=0) as web:
        ok, gone, local = await web.fetch_many(
            ["https://example.com/ok", "https://example.com/gone", "http://localhost/x"]
        )
    assert isinstance(ok, FetchedPage)
    assert isinstance(gone, FetchError) and gone.kind == "http" and "410" in gone.message
    assert isinstance(local, FetchError) and local.kind == "blocked_address"
