"""Shared HTTP plumbing for search providers."""

from __future__ import annotations

from typing import Any

import httpx

from mai.search.base import SearchError


async def get(
    client: httpx.AsyncClient, provider: str, url: str, params: dict[str, Any]
) -> httpx.Response:
    """GET that turns every transport or status failure into a SearchError."""
    try:
        response = await client.get(url, params=params)
    except httpx.HTTPError as error:
        raise SearchError(provider, f"request failed ({type(error).__name__})") from error
    if response.status_code == 429:
        raise SearchError(provider, "rate limited (HTTP 429); try again shortly")
    if response.status_code >= 400:
        raise SearchError(provider, f"HTTP {response.status_code}")
    return response


async def get_json(
    client: httpx.AsyncClient, provider: str, url: str, params: dict[str, Any]
) -> Any:
    response = await get(client, provider, url, params)
    try:
        return response.json()
    except ValueError as error:
        raise SearchError(provider, "response was not JSON") from error
