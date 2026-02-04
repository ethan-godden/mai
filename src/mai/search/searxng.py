"""General web search through a self-hosted SearXNG instance.

SearXNG is a metasearch engine: it forwards the query to public search engines
and merges their results. See docs/guide/search.md for running one locally
and docs/decisions/0003-search-backend.md for why.
"""

from __future__ import annotations

import httpx

from mai.search.base import SearchError, SearchResult, parse_date
from mai.search.http import get


class SearxngProvider:
    name = "searxng"

    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")

    async def search(self, query: str, *, max_results: int = 10) -> list[SearchResult]:
        try:
            response = await get(
                self._client, self.name, f"{self._base_url}/search", {"q": query, "format": "json"}
            )
        except SearchError as error:
            if "HTTP 403" in str(error):
                raise SearchError(
                    self.name,
                    "JSON output is disabled; add 'json' to search.formats in settings.yml",
                ) from error
            if "request failed" in str(error):
                raise SearchError(
                    self.name,
                    f"not reachable at {self._base_url}; start it with scripts/searxng.sh run",
                ) from error
            raise
        try:
            payload = response.json()
        except ValueError as error:
            raise SearchError(self.name, "response was not JSON") from error

        results = [
            SearchResult(
                url=item["url"],
                title=item.get("title") or item["url"],
                snippet=(item.get("content") or "").strip(),
                providers=(self.name,),
                engines=tuple(item.get("engines") or ()),
                published=parse_date(item.get("publishedDate")),
            )
            for item in payload.get("results", [])
            if item.get("url", "").startswith(("http://", "https://"))
        ]
        if not results and payload.get("unresponsive_engines"):
            # Every upstream engine failed, typically blocked or rate limited.
            failed = ", ".join(f"{name} ({why})" for name, why in payload["unresponsive_engines"])
            raise SearchError(self.name, f"no results; engines failed: {failed}")
        return results[:max_results]
