# 0003: Self-hosted SearXNG behind a provider interface

Date: 2026-11-23
Status: Accepted. See Outcome for what changed in implementation.

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

## Outcome (2026-11-23)

Implemented in milestone M2 with two changes from the decision above:

- **SearXNG is installed from its official source, not through SimpleXNG.**
  SimpleXNG vendors a copy of SearXNG that was last updated in August 2025.
  SearXNG's engines break whenever the public search engines change, so a
  copy more than a year old is a poor base. `scripts/searxng.sh` clones the
  official repository into `data/searxng/`, installs it into its own
  virtualenv with uv, and can update it. No Docker is involved.
- **Semantic Scholar is not usable without a key.** Every unauthenticated
  request made during implementation was refused with HTTP 429. The provider
  is implemented, accepts an optional free API key, and is tested only
  against the documented response format. OpenAlex was considered as a
  keyless replacement and rejected: without a key its requests share a very
  small daily budget across the whole network address.

Verified on the day: a test query returned 28 results through SearXNG from
Google and DuckDuckGo; Wikipedia and arXiv returned live results.

Only `searxng` is enabled by default. Wikipedia, arXiv and Semantic Scholar
are opt-in until the planner (M5) can choose providers per question.
