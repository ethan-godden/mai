# Using Mai

How to ask questions and read the results. Verified on 2026-11-23 on an M3
Max with 128 GB, Qwen 3.8 27B through Ollama.

Before the first use, follow [setup.md](setup.md): the model server and the
search service both need to be running. `uv run mai doctor` confirms they
are.

## Asking a question

```bash
uv run mai ask "Is it worth replacing a gas boiler with a heat pump in a cold climate?"
```

Mai shows what it is doing, streams the answer, then lists the sources:

```
Planning searches
Searching: heat pump vs gas boiler cost cold climate | cold climate heat pump COP ...
Reading 8 pages
Writing answer from 5 sources
Whether it is worth it depends on ... a financially prudent option for many
households despite higher upfront costs [3]. ...

Sources
[1] Heat Pump vs Boiler: Lifespan Comparison (Home Heat Pump Guide, 2026-03-18)
    https://homeheatpumpguide.co.uk/...
...
38.6s total (rewrite 3.8s · search 0.8s · read 2.6s · select 0.0s · answer 31.5s) · qwen3.8:27b
Saved to runs/20261123-022045-is-it-worth-replacing-a-gas-boiler
```

Progress and warnings go to standard error; the answer and sources go to
standard output.

| Option | Meaning |
|---|---|
| `-p, --provider NAME` | Search with this provider instead of the configured defaults; repeatable. See [search.md](search.md) |
| `--no-save` | Do not keep a record of the run |
| `--no-search` | Answer from the model alone, with no sources or citations |
| `--tier fast\|main\|heavy` | Model tier for `--no-search` |
| `-c, --config PATH` | Use a specific `config.toml` |

For a scholarly question, add the paper and encyclopedia providers:

```bash
uv run mai ask "How does retrieval-augmented generation reduce hallucination?" -p searxng -p arxiv -p wikipedia
```

## What happens

This is *quick mode*: one pass, meant to take well under a minute.

| Stage | What it does | Typical time |
|---|---|---|
| Rewrite | The fast model turns the question into 2 to 4 search queries | 2 to 5 s |
| Search | Every query goes to every enabled provider; results are merged | about 1 s |
| Read | The top 8 pages are fetched and cleaned, within a 20 s budget | 1 to 3 s |
| Select | Pages are split into passages and the 10 most relevant are kept, at most 3 per page | under 0.1 s |
| Answer | The model writes from those passages only, citing each claim | 20 to 35 s |

Measured totals on the reference machine were 38 to 44 seconds over three
runs with default settings. Most of the
answer stage is the model *reading* the roughly 3,000-token prompt (about 15
to 18 s), not writing.

The model never sees a whole page, only the selected passages, and it is told
to use nothing else. If no readable source is found, Mai says so and does not
ask the model at all, so it cannot answer from memory.

## Reading the answer

- **`[n]` is a citation.** It points to source *n* in the list below the
  answer. `[1][3]` means both sources support the sentence.
- **Only cited sources are listed.** Sources that were read but not used are
  in the run record.
- **"search summary only, page not read"** next to a source means the page
  itself could not be fetched and the answer relies on the search engine's
  one-line summary of it. Treat those claims with more caution.

### What is and is not checked

| Checked now | Not checked yet |
|---|---|
| Every citation number refers to a real source (a warning is shown otherwise) | Whether the cited source actually supports the sentence |
| An answer with no citations is flagged | Whether the sources themselves are reliable |
| The model is given only retrieved passages | Whether important sources were missed |

Local models make mistakes. A citation tells you where to look, not that the
claim is right. Automatic checking of each claim against its cited passage is
milestone M6; until then, open the source for anything that matters.

Passage selection in this version is by keyword match, so results lean
towards pages that repeat the question's words, and nothing yet weighs how
trustworthy a site is. Milestone M4 replaces this.

## Run records

Each run is saved in its own folder under `runs/` (git-ignored):

| File | Contents |
|---|---|
| `report.md` | The answer, its sources and any caveats, ready to read or share |
| `run.json` | The queries, every search result, each source with the exact passages the model saw and their scores, pages that could not be read and why, timings, and the settings used |
| `trace.jsonl` | One line per model call: the full prompt, the output, token counts and timing |

To see exactly what the model was shown for an answer, open `trace.jsonl` or
the `sources[].passages` in `run.json`. API keys are never written to a run
record. Delete a run by deleting its folder.

## Tuning

Under `[quick]` in `config.toml`; values shown are the defaults.

```toml
[quick]
results_per_query = 8      # search results kept per query
max_sources = 8            # pages read
read_budget_s = 20         # pages still loading after this are left out
max_passages = 10          # passages given to the model
passages_per_source = 3    # so one long page cannot crowd out the rest
passage_words = 220
answer_tier = "fast"       # "main" lets the model think first
answer_max_tokens = 1200
```

Measured effect of the two settings that matter most for speed, on one
question (2026-11-23):

| Setting | Prompt tokens | Time to first word | Answer stage |
|---|---|---|---|
| Defaults (10 passages, fast tier) | 2,990 | 17.6 s | 32.5 s |
| `max_passages = 6` | 1,577 | 9.0 s | 21.2 s |
| `answer_tier = "main"` (thinking on) | 2,702 | 15.0 s | 57.5 s |

Fewer passages is faster but gives the model less evidence. Thinking nearly
doubled the time; whether it improves answers has not been measured yet.

## Other commands

| Command | Purpose | Guide |
|---|---|---|
| `mai doctor` | Check that everything is ready | [setup.md](setup.md) |
| `mai search "..."` | List search results without reading them | [search.md](search.md) |
| `mai fetch URL` | Print one page or PDF as Markdown | [reading.md](reading.md) |

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| "I could not find any readable sources" | Search returned nothing or no page could be read. Run `mai doctor`; check the warnings printed above the message; try rephrasing |
| `searxng failed: not reachable` | Start the search service: `scripts/searxng.sh run` |
| `Model call failed` | The model server is down or the model name is wrong. `mai doctor` |
| "the answer cites [7], which match no source" | The model invented a citation number. Disregard that citation |
| "the answer contains no citations" | The model ignored the citation rule. Treat the answer as unsupported |
| The first answer after a pause is slow | The model was unloaded and is being loaded again |
| Answers take over a minute | Lower `max_passages`, or check nothing else is using the GPU |
