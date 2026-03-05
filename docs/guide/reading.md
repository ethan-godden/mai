# Reading pages

How Mai turns a URL into clean text: fetching, extraction, and the browser
fallback. Verified on 2026-11-23 on an M3 Max, macOS.

## From the command line

```bash
uv run mai fetch https://en.wikipedia.org/wiki/Retrieval-augmented_generation
```

The page's title, URL, site, author, date and word count are printed to
standard error and the Markdown text to standard output, so the text can be
piped or saved on its own:

```bash
uv run mai fetch https://arxiv.org/pdf/2005.11401 > paper.md
```

| Option | Meaning |
|---|---|
| `--max-chars N` | Print only the first N characters of the text |
| `--json` | Print the document and its metadata as JSON |

## What happens to a URL

```
URL ─▶ safety check ─▶ cache? ─▶ robots.txt ─▶ download ─▶ extract ─▶ Document
                                                              │
                                              no readable text (HTML only)
                                                              ▼
                                                   render in browser ─▶ extract
```

1. **Safety check.** Only `http` and `https` URLs are accepted, and private
   or local addresses are refused (see Safety below).
2. **Cache.** A copy fetched within the last 24 hours is reused.
3. **robots.txt.** The site's rules are fetched once and obeyed.
4. **Download**, with limits on time, size and content type.
5. **Extract** the main content as Markdown.
6. **Browser fallback**, only if the page came back with no readable text.

## What can be read

| Content | How | Result |
|---|---|---|
| HTML | [trafilatura](https://trafilatura.readthedocs.io) | Article text with headings and tables as Markdown; navigation, adverts, footers and scripts removed. Title, author, date and site name where the page declares them |
| PDF | pdfium (`pypdfium2`) | Text of up to 200 pages, with wrapped and hyphenated lines rejoined. Title, author and date from the PDF's metadata |
| Plain text, Markdown | passed through | As is |

Anything else (images, video, office documents, archives) is skipped.

Known limits:

- PDFs store lines, not paragraphs. Each page becomes one block of text and
  paragraph breaks within a page are lost. Tables and multi-column layouts
  may come out jumbled.
- A PDF with no title in its metadata gets its first 120 characters as a
  title.
- Scanned PDFs (images of pages) have no text layer and are reported as
  unreadable. There is no OCR.
- Pages behind a login or paywall yield whatever the site shows a logged-out
  visitor.

## Browser fallback

Some pages are an empty shell until their JavaScript runs. When a fetched
page has no readable text, Mai loads it in headless Chromium (through
[Playwright](https://playwright.dev/python/)), waits for it to settle, and
extracts the rendered result. A typical render takes a few seconds, so it is
used only when the plain fetch fails to find text.

It is optional. Install it once:

```bash
uv sync --extra browser
```

```bash
uv run playwright install --only-shell chromium
```

The second command downloads a headless Chromium (about 210 MB) into
`~/Library/Caches/ms-playwright`. `mai doctor` reports whether the fallback is
available. To turn it off, set `browser_fallback = false` under `[fetch]`.

After a plain `uv sync` (without `--extra browser`) the Playwright package is
removed again; include the extra whenever you sync if you want to keep it.

The browser is not used to get around a refusal. A page blocked by
robots.txt, an HTTP error such as 403, or a blocked address stays unread.

## Safety

The URLs Mai fetches come from search results, and the pages themselves are
untrusted. The rules are recorded in
[ADR 0006](../decisions/0006-fetching-untrusted-pages.md).

| Rule | Detail |
|---|---|
| No private or local addresses | `localhost`, loopback, private network ranges, link-local addresses, single-label hostnames and `.local`/`.internal` names are refused, including when a public page redirects to one |
| Browser requests are vetted too | Every request a rendered page makes passes the same check, so page scripts cannot reach services on this machine or network |
| robots.txt is obeyed | A missing robots.txt allows everything; a server error fetching it blocks the site for that run |
| Polite pacing | At most 8 downloads at once, and at least 1 second between requests to the same host |
| Honest identity | Requests are sent as `Mai/0.1 (local research agent)`, not disguised as a browser |
| Bounded | 20 second timeout, 20 MB size limit, at most 5 redirects |
| Pages are data | Page text never triggers actions; it only fills data slots in fixed prompts (see [ADR 0004](../decisions/0004-pipeline-over-agent-framework.md)) |

Limit of the address check: it inspects the URL and does not resolve DNS, so
a public hostname that resolves to a private address is not caught.

To read pages on your own machine or network (local documentation, for
example), set `allow_private_addresses = true` under `[fetch]`.

## Configuration

All under `[fetch]` in `config.toml`; values shown are the defaults.

```toml
[fetch]
user_agent = "Mai/0.1 (local research agent)"
concurrency = 8
per_host_interval_s = 1.0
timeout_s = 20
max_bytes = 20000000
cache_ttl_hours = 24
respect_robots = true
browser_fallback = true
allow_private_addresses = false
```

The page cache lives in `data/cache/pages/` (git-ignored). Delete that
directory to clear it, or set `cache_ttl_hours = 0` to disable caching.

## Troubleshooting

| Message | Meaning |
|---|---|
| `Disallowed by robots.txt` | The site asks automated clients not to read that page |
| `HTTP 403` | The site refuses non-browser clients. Mai does not work around this |
| `Refusing to fetch a private or local address` | See Safety above |
| `No readable article text found` | The page has no article text even after rendering, or the browser fallback is not installed |
| `PDF has no extractable text` | A scanned PDF |
| `Unsupported content type` | Not HTML, PDF or text |
| `Browser could not start` | Run `uv run playwright install --only-shell chromium` |
