"""Headless-browser rendering for pages that build themselves with JavaScript.

Plain fetching is fast and is tried first. When it returns a page with no
readable text, ``mai.read`` asks a ``Renderer`` to load the page in a real
browser, let its scripts run, and hand back the resulting HTML.

The browser is optional: it needs the ``browser`` extra and a one-time
Chromium download (docs/guide/setup.md). Without it Mai still works, and
JavaScript-only pages are reported as unreadable.

The same safety rules as ``mai.fetch`` apply: every request the page makes is
checked, so a page cannot make the browser call private or local addresses.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
from datetime import UTC, datetime
from typing import Any, Protocol

from mai.config import FetchSettings
from mai.fetch import FetchedPage, FetchError, check_url

SETTLE_TIMEOUT_MS = 5_000
"""How long to wait for network activity to stop after the page has loaded."""

MAX_CONCURRENT_PAGES = 2
SKIPPED_RESOURCES = frozenset({"image", "media", "font"})
"""Not needed to read a page's text, and skipping them makes rendering much faster."""


class Renderer(Protocol):
    async def render(self, url: str) -> FetchedPage:
        """Load ``url`` in a browser and return the rendered HTML. Raises FetchError."""
        ...

    async def aclose(self) -> None: ...


def playwright_installed() -> bool:
    return importlib.util.find_spec("playwright") is not None


def make_renderer(settings: FetchSettings) -> Renderer | None:
    """Return a renderer, or None if the fallback is disabled or not installed."""
    if not settings.browser_fallback or not playwright_installed():
        return None
    return PlaywrightRenderer(settings)


class PlaywrightRenderer:
    """Renders pages in headless Chromium. The browser starts on first use."""

    def __init__(self, settings: FetchSettings) -> None:
        self._settings = settings
        self._start_lock = asyncio.Lock()
        self._slots = asyncio.Semaphore(MAX_CONCURRENT_PAGES)
        self._playwright: Any = None
        self._browser: Any = None

    async def __aenter__(self) -> PlaywrightRenderer:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def _ensure_browser(self) -> Any:
        from playwright.async_api import Error, async_playwright

        async with self._start_lock:
            if self._browser is None:
                try:
                    self._playwright = await async_playwright().start()
                    self._browser = await self._playwright.chromium.launch(headless=True)
                except Error as error:
                    await self.aclose()
                    raise FetchError(
                        "",
                        "network",
                        "Browser could not start; run 'uv run playwright install "
                        f"--only-shell chromium' ({_first_line(error)})",
                    ) from error
            return self._browser

    async def _guard(self, route: Any) -> None:
        """Vet every request the page makes, not just the page itself."""
        request = route.request
        if request.resource_type in SKIPPED_RESOURCES:
            await route.abort()
            return
        try:
            check_url(request.url, allow_private=self._settings.allow_private_addresses)
        except FetchError:
            await route.abort()
            return
        await route.continue_()

    async def render(self, url: str) -> FetchedPage:
        from playwright.async_api import Error, TimeoutError

        check_url(url, allow_private=self._settings.allow_private_addresses)
        browser = await self._ensure_browser()
        async with self._slots:
            # A fresh context per page: no cookies or storage shared between sites.
            context = await browser.new_context(
                user_agent=self._settings.user_agent, accept_downloads=False
            )
            try:
                await context.route("**/*", self._guard)
                page = await context.new_page()
                response = await page.goto(
                    url, wait_until="domcontentloaded", timeout=self._settings.timeout_s * 1000
                )
                # Some pages never go quiet; then use what has rendered so far.
                with contextlib.suppress(TimeoutError):
                    await page.wait_for_load_state("networkidle", timeout=SETTLE_TIMEOUT_MS)
                status = response.status if response else 200
                if status >= 400:
                    raise FetchError(url, "http", f"HTTP {status}")
                body = (await page.content()).encode()
                if len(body) > self._settings.max_bytes:
                    raise FetchError(
                        url, "too_large", f"Larger than {self._settings.max_bytes} bytes"
                    )
                return FetchedPage(
                    url=url,
                    final_url=page.url,
                    status=status,
                    content_type="text/html",
                    charset="utf-8",
                    body=body,
                    fetched_at=datetime.now(UTC),
                )
            except TimeoutError as error:
                raise FetchError(url, "timeout", "Timed out in browser") from error
            except Error as error:
                raise FetchError(
                    url, "network", f"Browser could not load page ({_first_line(error)})"
                ) from error
            finally:
                await context.close()


def _first_line(error: Exception) -> str:
    return str(error).strip().splitlines()[0][:160] if str(error).strip() else type(error).__name__
