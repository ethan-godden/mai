"""Encyclopedia search through the public Wikipedia API (no key needed)."""

from __future__ import annotations

import html
import re
from urllib.parse import quote

import httpx

from mai.search.base import SearchResult
from mai.search.http import get_json

_TAG = re.compile(r"<[^>]+>")


class WikipediaProvider:
    name = "wikipedia"

    def __init__(self, client: httpx.AsyncClient, language: str = "en") -> None:
        self._client = client
        self._site = f"https://{language}.wikipedia.org"

    async def search(self, query: str, *, max_results: int = 10) -> list[SearchResult]:
        payload = await get_json(
            self._client,
            self.name,
            f"{self._site}/w/api.php",
            {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": min(max_results, 50),
                "srprop": "snippet",
                "format": "json",
            },
        )
        return [
            SearchResult(
                url=f"{self._site}/wiki/{quote(item['title'].replace(' ', '_'), safe='()_,:')}",
                title=item["title"],
                # Snippets arrive as HTML with the matched words wrapped in spans.
                snippet=html.unescape(_TAG.sub("", item.get("snippet", ""))).strip(),
                providers=(self.name,),
                # The API's timestamp is the last edit, not a publication date, so it is not used.
            )
            for item in payload.get("query", {}).get("search", [])
        ]
