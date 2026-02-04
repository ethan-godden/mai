"""Web and specialist search. See docs/guide/search.md."""

from __future__ import annotations

from collections.abc import Callable, Sequence

import httpx

from mai.config import SearchSettings
from mai.search.base import (
    USER_AGENT,
    SearchError,
    SearchOutcome,
    SearchProvider,
    SearchResult,
    canonical_url,
    merge_results,
    search_all,
)
from mai.search.searxng import SearxngProvider

__all__ = [
    "PROVIDERS",
    "SearchError",
    "SearchOutcome",
    "SearchProvider",
    "SearchResult",
    "build_providers",
    "canonical_url",
    "http_client",
    "merge_results",
    "search_all",
]

ProviderFactory = Callable[[httpx.AsyncClient, SearchSettings], SearchProvider]

PROVIDERS: dict[str, ProviderFactory] = {
    "searxng": lambda client, settings: SearxngProvider(client, settings.searxng_url),
}
"""Every provider Mai knows, by the name used in config and on the command line."""


def http_client(
    settings: SearchSettings, *, transport: httpx.AsyncBaseTransport | None = None
) -> httpx.AsyncClient:
    """HTTP client configured for search providers."""
    return httpx.AsyncClient(
        transport=transport,
        timeout=settings.timeout_s,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )


def build_providers(
    client: httpx.AsyncClient, settings: SearchSettings, names: Sequence[str] | None = None
) -> list[SearchProvider]:
    """Instantiate the named providers (default: those enabled in settings)."""
    chosen = list(names or settings.providers)
    unknown = sorted(set(chosen) - set(PROVIDERS))
    if unknown:
        raise ValueError(
            f"Unknown search provider(s): {', '.join(unknown)}. "
            f"Available: {', '.join(sorted(PROVIDERS))}."
        )
    return [PROVIDERS[name](client, settings) for name in dict.fromkeys(chosen)]
