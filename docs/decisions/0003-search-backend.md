# 0003: Self-hosted SearXNG behind a provider interface

Date: 2026-11-23
Status: Accepted

## Context

Research needs web search. The project's aim is to avoid metered services, so
a paid search API as the only option would defeat the purpose. Docker is not
installed on the target machine.

## Decision

1. Define a `SearchProvider` interface; the pipeline queries several providers
   in parallel and merges results.
2. The default general-web provider is a local **SearXNG** instance (a
   metasearch engine aggregating public search engines), queried through its
   JSON API.
3. Run SearXNG without Docker, as a Python process. The `simplexng` package
   (installed with `uv tool install`) is the first option to try; a manual
   virtualenv install from source is the fallback. The working method is
   documented in `docs/guide/search.md` in M2.
4. Add keyless specialist providers: arXiv, Wikipedia, Semantic Scholar.
5. Leave room for an optional paid provider (for example Brave or Tavily),
   off by default.

## Alternatives considered

- **Paid search API only**: best reliability, but metered and sends every
  query to one vendor.
- **Scraping one search engine directly**: fragile and against terms of
  service.
- **DuckDuckGo unofficial library**: simple, but a single rate-limited source.
- **Installing Docker for SearXNG**: works, but adds a heavy dependency for
  one service.

## Consequences

- Free and private by default, with engine diversity.
- Upstream engines can rate-limit or block a metasearch instance; mitigated
  by caching, multiple engines, specialist providers, and the optional paid
  fallback.
- One more local service to run; covered by `mai doctor`.

## Sources

- [SimpleXNG on PyPI](https://pypi.org/project/simplexng/)
- [SearXNG step-by-step installation](https://docs.searxng.org/admin/installation-searxng.html)
