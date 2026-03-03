"""Read a URL: fetch it and extract its text. The pipeline's view of "a page"."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import replace

from mai.extract import Document, ExtractError, extract
from mai.fetch import Fetcher, FetchError
from mai.render import Renderer

ReadError = FetchError | ExtractError
"""Why a URL could not be read."""


async def read_url(fetcher: Fetcher, url: str, renderer: Renderer | None = None) -> Document:
    """Fetch ``url`` and extract its main content. Raises FetchError or ExtractError.

    If the plain fetch yields a page with no readable text and a ``renderer``
    is given, the page is rendered in a browser and extraction is tried again.
    The browser is never used to get around a refusal (robots.txt, an HTTP
    error, a blocked address): only for pages that were served but are empty
    until their scripts run.
    """
    page = await fetcher.fetch(url)
    try:
        # Extraction is CPU-bound; keep it off the event loop so other fetches proceed.
        return await asyncio.to_thread(extract, page)
    except ExtractError as error:
        if renderer is None or not error.needs_browser:
            raise
    rendered = replace(await renderer.render(page.final_url), url=page.url)
    document = await asyncio.to_thread(extract, rendered)
    fetcher.remember(rendered)  # only once it proved readable
    return document


async def read_many(
    fetcher: Fetcher, urls: Sequence[str], renderer: Renderer | None = None
) -> list[Document | ReadError]:
    """Read several URLs concurrently. Failures are returned in place, not raised."""

    async def one(url: str) -> Document | ReadError:
        try:
            return await read_url(fetcher, url, renderer)
        except (FetchError, ExtractError) as error:
            return error

    return list(await asyncio.gather(*(one(url) for url in urls)))
