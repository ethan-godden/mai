"""Read a URL: fetch it and extract its text. The pipeline's view of "a page"."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from mai.extract import Document, ExtractError, extract
from mai.fetch import Fetcher, FetchError

ReadError = FetchError | ExtractError
"""Why a URL could not be read."""


async def read_url(fetcher: Fetcher, url: str) -> Document:
    """Fetch ``url`` and extract its main content. Raises FetchError or ExtractError."""
    page = await fetcher.fetch(url)
    # Extraction is CPU-bound; keep it off the event loop so other fetches proceed.
    return await asyncio.to_thread(extract, page)


async def read_many(fetcher: Fetcher, urls: Sequence[str]) -> list[Document | ReadError]:
    """Read several URLs concurrently. Failures are returned in place, not raised."""

    async def one(url: str) -> Document | ReadError:
        try:
            return await read_url(fetcher, url)
        except (FetchError, ExtractError) as error:
            return error

    return list(await asyncio.gather(*(one(url) for url in urls)))
