"""Scholarly paper search through the Semantic Scholar Graph API.

Works without a key, but unauthenticated requests share one global rate limit
and are often refused with HTTP 429. A free API key (set
``search.semantic_scholar_api_key``) gives a dedicated limit.
"""

from __future__ import annotations

from typing import Any

import httpx

from mai.search.base import SearchResult, parse_date
from mai.search.http import get_json

API_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
FIELDS = "title,abstract,url,year,publicationDate,externalIds"


def paper_url(paper: dict[str, Any]) -> str:
    """Prefer the paper's own home (arXiv, then DOI) over its Semantic Scholar page,
    so the same paper found by another provider merges with this result."""
    ids = paper.get("externalIds") or {}
    if ids.get("ArXiv"):
        return f"https://arxiv.org/abs/{ids['ArXiv']}"
    if ids.get("DOI"):
        return f"https://doi.org/{ids['DOI']}"
    return paper["url"]


class SemanticScholarProvider:
    name = "semantic_scholar"

    def __init__(self, client: httpx.AsyncClient, api_key: str = "") -> None:
        self._client = client
        self._api_key = api_key

    async def search(self, query: str, *, max_results: int = 10) -> list[SearchResult]:
        params: dict[str, Any] = {"query": query, "limit": min(max_results, 100), "fields": FIELDS}
        payload = await get_json(
            self._client,
            self.name,
            API_URL,
            params,
            headers={"x-api-key": self._api_key} if self._api_key else None,
        )
        return [
            SearchResult(
                url=paper_url(paper),
                title=paper.get("title") or "(untitled)",
                snippet=(paper.get("abstract") or "").strip(),
                providers=(self.name,),
                published=parse_date(paper.get("publicationDate"))
                or parse_date(f"{paper['year']}-01-01" if paper.get("year") else None),
            )
            for paper in payload.get("data") or []
            if paper.get("url")
        ]
