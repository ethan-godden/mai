"""Polite, cached page fetching.

``Fetcher`` downloads pages for the research pipeline. It is deliberately
conservative, because the URLs it is given come from search results and are
therefore untrusted:

* only ``http``/``https`` URLs, and never private or loopback addresses,
  including after a redirect;
* ``robots.txt`` is respected;
* requests to one host are spaced out, and total concurrency is capped;
* responses are limited in size and to content types Mai can read;
* successful responses are cached on disk, so repeating a run costs nothing.

Fetching returns raw bytes. Turning them into text is ``mai.extract``'s job.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import urldefrag, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from mai.config import FetchSettings

ROBOTS_AGENT = "Mai"
"""The product token matched against robots.txt rules."""

ALLOWED_TYPES = frozenset(
    {
        "text/html",
        "application/xhtml+xml",
        "text/plain",
        "text/markdown",
        "application/pdf",
    }
)
_BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")

FailureKind = Literal[
    "invalid_url", "blocked_address", "robots", "http", "timeout", "network", "too_large", "type"
]


class FetchError(Exception):
    """A page could not be fetched. ``kind`` says why, for logging and reports."""

    def __init__(self, url: str, kind: FailureKind, message: str) -> None:
        super().__init__(f"{message}: {url}")
        self.url = url
        self.kind: FailureKind = kind
        self.message = message


@dataclass(frozen=True)
class FetchedPage:
    url: str
    """The URL that was asked for."""
    final_url: str
    """Where it ended up after redirects."""
    status: int
    content_type: str
    """Media type in lower case without parameters, e.g. ``text/html``."""
    charset: str | None
    body: bytes
    fetched_at: datetime
    from_cache: bool = False


def check_url(url: str, *, allow_private: bool = False) -> None:
    """Raise FetchError unless ``url`` is a public http(s) address.

    This guards against search results or redirects that point at services on
    this machine or the local network. It inspects the URL only and does not
    resolve DNS, so it does not stop a public name that resolves to a private
    address.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        raise FetchError(url, "invalid_url", "Not an http(s) URL")
    if allow_private:
        return
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        blocked = host == "localhost" or host.endswith(_BLOCKED_HOST_SUFFIXES) or "." not in host
    else:
        blocked = not address.is_global
    if blocked:
        raise FetchError(url, "blocked_address", "Refusing to fetch a private or local address")


class _HostGate:
    """Spaces out requests to one host."""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.last_request = float("-inf")


class Fetcher:
    def __init__(
        self,
        settings: FetchSettings,
        cache_dir: Path,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._settings = settings
        self._cache_dir = cache_dir
        self._sleep = sleep
        self._monotonic = monotonic
        self._now = now
        self._slots = asyncio.Semaphore(settings.concurrency)
        self._gates: dict[str, _HostGate] = {}
        self._robots: dict[str, asyncio.Task[RobotFileParser | None]] = {}
        self._http = httpx.AsyncClient(
            transport=transport,
            timeout=settings.timeout_s,
            headers={"User-Agent": settings.user_agent, "Accept": "text/html,*/*;q=0.8"},
            follow_redirects=True,
            max_redirects=5,
            event_hooks={"request": [self._check_request]},
        )

    async def __aenter__(self) -> Fetcher:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def fetch(self, url: str) -> FetchedPage:
        """Fetch one page, from cache if a fresh copy exists. Raises FetchError."""
        url = urldefrag(url.strip()).url
        check_url(url, allow_private=self._settings.allow_private_addresses)
        cached = self._read_cache(url)
        if cached is not None:
            return cached
        async with self._slots:
            if self._settings.respect_robots and not await self._allowed_by_robots(url):
                raise FetchError(url, "robots", "Disallowed by robots.txt")
            page = await self._download(url)
        self._write_cache(page)
        return page

    async def fetch_many(self, urls: Sequence[str]) -> list[FetchedPage | FetchError]:
        """Fetch several pages concurrently. Failures are returned in place, not raised."""

        async def one(url: str) -> FetchedPage | FetchError:
            try:
                return await self.fetch(url)
            except FetchError as error:
                return error

        return list(await asyncio.gather(*(one(url) for url in urls)))

    def remember(self, page: FetchedPage) -> None:
        """Cache a page obtained another way (a browser render), under its requested URL."""
        self._write_cache(page)

    # --- network ---------------------------------------------------------------

    async def _check_request(self, request: httpx.Request) -> None:
        # Runs for every request including each redirect hop.
        check_url(str(request.url), allow_private=self._settings.allow_private_addresses)

    async def _wait_for_host(self, url: str) -> None:
        host = urlsplit(url).netloc.lower()
        gate = self._gates.setdefault(host, _HostGate())
        async with gate.lock:
            wait = gate.last_request + self._settings.per_host_interval_s - self._monotonic()
            if wait > 0:
                await self._sleep(wait)
            gate.last_request = self._monotonic()

    async def _download(self, url: str) -> FetchedPage:
        await self._wait_for_host(url)
        limit = self._settings.max_bytes
        try:
            async with self._http.stream("GET", url) as response:
                if response.status_code >= 400:
                    raise FetchError(url, "http", f"HTTP {response.status_code}")
                content_type = (
                    response.headers.get("content-type", "").split(";")[0].strip().lower()
                )
                if content_type not in ALLOWED_TYPES:
                    raise FetchError(url, "type", f"Unsupported content type '{content_type}'")
                declared = response.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > limit:
                    raise FetchError(url, "too_large", f"Larger than {limit} bytes")
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > limit:
                        raise FetchError(url, "too_large", f"Larger than {limit} bytes")
                    chunks.append(chunk)
                return FetchedPage(
                    url=url,
                    final_url=str(response.url),
                    status=response.status_code,
                    content_type=content_type,
                    charset=response.charset_encoding,
                    body=b"".join(chunks),
                    fetched_at=self._now(),
                )
        except httpx.TimeoutException as error:
            raise FetchError(url, "timeout", "Timed out") from error
        except httpx.TooManyRedirects as error:
            raise FetchError(url, "network", "Too many redirects") from error
        except httpx.HTTPError as error:
            raise FetchError(url, "network", f"Request failed ({type(error).__name__})") from error

    # --- robots.txt --------------------------------------------------------------

    async def _allowed_by_robots(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        # One shared task per origin, so concurrent fetches do not each request robots.txt.
        task = self._robots.get(origin)
        if task is None:
            task = self._robots[origin] = asyncio.create_task(self._load_robots(origin))
        rules = await task
        return rules is None or rules.can_fetch(ROBOTS_AGENT, url)

    async def _load_robots(self, origin: str) -> RobotFileParser | None:
        """Return the site's rules, or None when everything is allowed.

        Following common crawler practice: a missing robots.txt (4xx) or one that
        cannot be reached allows everything, while a server error (5xx) means the
        site is in trouble and nothing is fetched.
        """
        rules = RobotFileParser()
        try:
            response = await self._http.get(f"{origin}/robots.txt")
        except (httpx.HTTPError, FetchError):
            return None
        if response.status_code >= 500:
            rules.parse(["User-agent: *", "Disallow: /"])
            return rules
        if response.status_code >= 400:
            return None
        rules.parse(response.text.splitlines())
        return rules

    # --- cache -------------------------------------------------------------------

    def _cache_paths(self, url: str) -> tuple[Path, Path]:
        key = hashlib.sha256(url.encode()).hexdigest()
        return self._cache_dir / f"{key}.json", self._cache_dir / f"{key}.body"

    def _read_cache(self, url: str) -> FetchedPage | None:
        meta_path, body_path = self._cache_paths(url)
        try:
            meta = json.loads(meta_path.read_text())
            fetched_at = datetime.fromisoformat(meta.pop("fetched_at"))
            if self._now() - fetched_at > timedelta(hours=self._settings.cache_ttl_hours):
                return None
            return FetchedPage(
                **meta, body=body_path.read_bytes(), fetched_at=fetched_at, from_cache=True
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None  # absent or unreadable: fetch again

    def _write_cache(self, page: FetchedPage) -> None:
        if self._settings.cache_ttl_hours <= 0:
            return
        meta_path, body_path = self._cache_paths(page.url)
        meta = asdict(page)
        del meta["body"], meta["from_cache"]
        meta["fetched_at"] = page.fetched_at.isoformat()
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        body_path.write_bytes(page.body)
        meta_path.write_text(json.dumps(meta))
