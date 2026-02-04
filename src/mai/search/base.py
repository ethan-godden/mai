"""Search provider interface and result merging.

A provider turns a query into a ranked list of ``SearchResult``. The pipeline
queries several providers at once through ``search_all``, which tolerates
individual failures and merges the lists into one ranking.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict

USER_AGENT = "Mai/0.1 (local research agent)"

RRF_K = 60
"""Reciprocal rank fusion constant; 60 is the value from the original paper."""

_TRACKING_PARAMS = frozenset({"fbclid", "gclid", "msclkid", "mc_cid", "mc_eid", "ref", "ref_src"})


class SearchError(Exception):
    """A provider could not answer a query."""

    def __init__(self, provider: str, message: str) -> None:
        super().__init__(f"{provider}: {message}")
        self.provider = provider


class SearchResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    url: str
    title: str
    snippet: str = ""
    providers: tuple[str, ...]
    """Which providers returned this result; more than one after merging."""
    engines: tuple[str, ...] = ()
    """Upstream engines behind a metasearch result, e.g. ("google", "duckduckgo")."""
    published: date | None = None


class SearchProvider(Protocol):
    name: str

    async def search(self, query: str, *, max_results: int = 10) -> list[SearchResult]:
        """Return results best first. Raise SearchError if the query cannot be answered."""
        ...


@dataclass(frozen=True)
class SearchOutcome:
    results: list[SearchResult]
    errors: dict[str, str] = field(default_factory=dict)
    """Provider name to error message, for providers that failed."""


def parse_date(value: object) -> date | None:
    """Parse the date formats providers use; None for anything unrecognised."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    for candidate in (text, text[:10]):
        try:
            return datetime.fromisoformat(candidate).date()
        except ValueError:
            continue
    return None


def canonical_url(url: str) -> str:
    """Normalise a URL so the same page from two providers compares equal."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower().removeprefix("www.")
    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"
    query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_PARAMS
        ]
    )
    return urlunsplit(("https", host, parts.path.rstrip("/"), query, ""))


def merge_results(
    ranked_lists: Sequence[list[SearchResult]], max_results: int
) -> list[SearchResult]:
    """Merge provider rankings with reciprocal rank fusion.

    A page's score is the sum of ``1 / (RRF_K + rank)`` over the lists it
    appears in, so pages several providers agree on rise to the top without
    needing the providers' own scores to be comparable.
    """
    scores: dict[str, float] = {}
    merged: dict[str, SearchResult] = {}
    for results in ranked_lists:
        seen: set[str] = set()
        for rank, result in enumerate(results, start=1):
            key = canonical_url(result.url)
            if key in seen:  # a provider listing one page twice gets no extra credit
                continue
            seen.add(key)
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank)
            known = merged.get(key)
            if known is None:
                merged[key] = result
            else:
                merged[key] = known.model_copy(
                    update={
                        "snippet": max(known.snippet, result.snippet, key=len),
                        "providers": tuple(dict.fromkeys(known.providers + result.providers)),
                        "engines": tuple(dict.fromkeys(known.engines + result.engines)),
                        "published": known.published or result.published,
                    }
                )
    # Python's sort is stable, so ties keep first-seen order.
    ordered = sorted(merged, key=lambda key: scores[key], reverse=True)
    return [merged[key] for key in ordered[:max_results]]


async def search_all(
    providers: Sequence[SearchProvider], query: str, *, max_results: int = 10
) -> SearchOutcome:
    """Query every provider concurrently and merge what comes back.

    One provider failing does not fail the search; its error is reported in
    the outcome so the caller can show or log it.
    """
    answers = await asyncio.gather(
        *(provider.search(query, max_results=max_results) for provider in providers),
        return_exceptions=True,
    )
    ranked: list[list[SearchResult]] = []
    errors: dict[str, str] = {}
    for provider, answer in zip(providers, answers, strict=True):
        if isinstance(answer, SearchError):
            errors[provider.name] = str(answer).removeprefix(f"{provider.name}: ")
        elif isinstance(answer, BaseException):
            raise answer
        else:
            ranked.append(answer)
    return SearchOutcome(merge_results(ranked, max_results), errors)
