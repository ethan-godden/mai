# Search

How Mai finds sources, and how to run the local search service. Verified on
2026-11-23 (SearXNG commit `a7163d0`, macOS, M3 Max).

## Providers

Mai queries one or more *providers* and merges their results.

| Name | Covers | Needs |
|---|---|---|
| `searxng` | The general web, through public search engines | A local SearXNG instance (below) |

Providers enabled by default are set in `config.toml`:

```toml
[search]
providers = ["searxng"]
searxng_url = "http://localhost:8888"
max_results = 10
timeout_s = 20
```

## Running SearXNG

[SearXNG](https://docs.searxng.org) is an open-source metasearch engine: it
sends your query to public search engines (Google, DuckDuckGo, Bing and
others) and merges what they return. Mai runs a private copy on this machine,
so no search API key or account is needed
([ADR 0003](../decisions/0003-search-backend.md)).

Set it up once. This clones the official SearXNG repository into
`data/searxng/` (git-ignored, about 120 MB), creates a virtualenv for it and
writes a settings file:

```bash
scripts/searxng.sh setup
```

Start it and leave it running in its own terminal tab:

```bash
scripts/searxng.sh run
```

Check it:

```bash
uv run mai doctor
```

To pick up engine fixes later (public engines change often, and SearXNG is
updated to keep up):

```bash
scripts/searxng.sh update
```

What the script configures, in `data/searxng/settings.yml`:

| Setting | Value | Why |
|---|---|---|
| `server.bind_address` | `127.0.0.1` | Reachable from this machine only |
| `server.port` | `8888` | Matches Mai's default `searxng_url` |
| `server.limiter` | `false` | The rate limiter protects public instances; this one is private |
| `search.formats` | `html`, `json` | Mai needs the JSON API, which is off by default |
| `server.secret_key` | random | Generated at setup |

You can also open <http://localhost:8888> in a browser and use it directly.
To choose which engines are used, edit `settings.yml` (see the
[SearXNG settings reference](https://docs.searxng.org/admin/settings/)) and
restart.

## Searching from the command line

```bash
uv run mai search "heat pump efficiency in cold climates"
```

| Option | Meaning |
|---|---|
| `-p, --provider NAME` | Use this provider instead of the configured defaults; repeatable |
| `-n, --max-results N` | Number of merged results |
| `--json` | Machine-readable output |

`mai search` lists results only. It does not read the pages; that is
`mai fetch`.

## How results are merged

Each provider returns a ranked list. Mai combines them with *reciprocal rank
fusion*: a page scores `1 / (60 + rank)` in every list it appears in, and the
scores are added. Pages that several providers agree on rise to the top, and
no provider's internal scoring has to be comparable with another's.

Before merging, URLs are normalised so the same page is recognised across
providers: the scheme, a leading `www.`, a trailing slash, the fragment and
tracking parameters (`utm_*`, `fbclid`, `gclid` and similar) are ignored.

If a provider fails, the others still answer and the failure is reported on
standard error.

## Privacy

Your query text is sent to the public search engines SearXNG forwards it to,
from your IP address. Nothing is sent to any LLM service. SearXNG itself keeps
no search history.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `searxng failed: not reachable` | It is not running. `scripts/searxng.sh run`. |
| `JSON output is disabled` | `json` is missing from `search.formats` in `settings.yml`. |
| `no results; engines failed: ...` | Upstream engines are blocking or rate-limiting. Wait a few minutes, run `scripts/searxng.sh update`, or enable other engines in `settings.yml`. |
| Port 8888 already in use | Set `MAI_SEARXNG_PORT` before `setup`, and the same port in `searxng_url`. |
| Startup log shows `can't register engine` errors | Harmless: some optional engines need extra software. |
