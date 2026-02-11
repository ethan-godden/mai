"""Preprint search through the public arXiv API (no key needed).

arXiv asks clients to make no more than one request every three seconds, so
requests from this provider are spaced out.
"""

from __future__ import annotations

import asyncio
import re
import time
import xml.etree.ElementTree as ElementTree

import httpx

from mai.search.base import SearchError, SearchResult, parse_date
from mai.search.http import get

API_URL = "https://export.arxiv.org/api/query"
MIN_INTERVAL_S = 3.0
ATOM = {"atom": "http://www.w3.org/2005/Atom"}

# arXiv ORs bare words together, which returns loosely related papers, so the
# query is rewritten to require every meaningful word.
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "how",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "to",
        "was",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "with",
    ]
)
_VERSION = re.compile(r"v\d+$")


def build_query(query: str) -> str:
    words = [word for word in re.findall(r"[\w-]+", query) if word.lower() not in _STOPWORDS]
    return " AND ".join(f"all:{word}" for word in words or re.findall(r"[\w-]+", query))


class ArxivProvider:
    name = "arxiv"

    def __init__(
        self, client: httpx.AsyncClient, *, min_interval_s: float = MIN_INTERVAL_S
    ) -> None:
        self._client = client
        self._min_interval_s = min_interval_s
        self._lock = asyncio.Lock()
        self._last_request = float("-inf")

    async def search(self, query: str, *, max_results: int = 10) -> list[SearchResult]:
        async with self._lock:
            wait = self._last_request + self._min_interval_s - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                response = await get(
                    self._client,
                    self.name,
                    API_URL,
                    {
                        "search_query": build_query(query),
                        "start": 0,
                        "max_results": max_results,
                        "sortBy": "relevance",
                    },
                )
            finally:
                self._last_request = time.monotonic()
        try:
            feed = ElementTree.fromstring(response.text)
        except ElementTree.ParseError as error:
            raise SearchError(self.name, "response was not valid XML") from error

        results = []
        for entry in feed.findall("atom:entry", ATOM):
            identifier = entry.findtext("atom:id", "", ATOM)
            if "/abs/" not in identifier:
                continue
            results.append(
                SearchResult(
                    # Link to the abstract page without a version, so it always shows the latest.
                    url=_VERSION.sub("", identifier.replace("http://", "https://")),
                    title=" ".join(entry.findtext("atom:title", "", ATOM).split()),
                    snippet=" ".join(entry.findtext("atom:summary", "", ATOM).split()),
                    providers=(self.name,),
                    published=parse_date(entry.findtext("atom:published", "", ATOM)),
                )
            )
        return results
