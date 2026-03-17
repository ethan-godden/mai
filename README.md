# Mai

A local-first research agent. Mai answers open-ended research questions the way
Perplexity does (search the web, read the sources, write a cited answer) but
runs every model on this machine, using open-weight models from Hugging Face.

The goal is to move research work off paid, token-metered services such as
Claude and onto local hardware, without giving up cited, checkable answers.

**Status:** milestones M1 to M3 are done. `mai ask` searches the web, reads the
best pages and writes a short answer with numbered citations, in about 35 to
45 seconds, using only local models. Better retrieval (M4), multi-step deep
research (M5) and automatic citation checking (M6) are next. See the [roadmap](docs/PLAN.md).

## Quick start

Full instructions: [docs/guide/setup.md](docs/guide/setup.md).

```bash
uv sync
cp config.example.toml config.toml   # then set the model names
scripts/searxng.sh setup             # once: install the local search service
scripts/searxng.sh run               # leave running in its own terminal tab
uv run mai doctor
uv run mai ask "Is a heat pump worth it in a cold climate?"
```

| Command | What it does |
|---|---|
| `mai doctor` | Checks machine, config, model server and models |
| `mai ask "..."` | Answers from the web with numbered citations |
| `mai search "..."` | Searches the web and lists merged results |
| `mai fetch URL` | Reads a page or PDF and prints it as Markdown |

## Target machine

| | |
|---|---|
| Hardware | MacBook Pro, Apple M3 Max, 128 GB unified memory |
| OS | macOS |
| Network | Needed for web search and page fetching only. All inference is local. |
| Disk | Models (tens of GB each), SearXNG (about 120 MB), optional headless browser (about 210 MB) |

## How it will work

```
question ─▶ plan ─▶ search ─▶ fetch & extract ─▶ index & rerank ─▶ read ─┐
              ▲                                                          │
              └────────────── gaps found? search again ◀────────────────┘
                                                                         │
                          cited report ◀─ verify citations ◀─ synthesise ◀┘
```

Two modes are planned:

- **Quick**: one search pass, a short cited answer in under a minute.
- **Deep**: iterative plan / search / read loop, a long structured report in
  minutes.

## Documentation

Documentation is a first-class deliverable of this project. Every milestone
updates the docs in the same commit series as the code.

| Document | What it covers |
|---|---|
| [docs/guide/setup.md](docs/guide/setup.md) | Installing, configuring and troubleshooting |
| [docs/guide/usage.md](docs/guide/usage.md) | Asking questions, reading answers and citations, run records, tuning |
| [docs/guide/search.md](docs/guide/search.md) | Search providers and running the local SearXNG service |
| [docs/guide/reading.md](docs/guide/reading.md) | Fetching and extracting pages, browser fallback, safety rules |
| [docs/PLAN.md](docs/PLAN.md) | Goals, scope, milestones, exit criteria, risks |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Components, data flow, data model, repository layout |
| [docs/ML.md](docs/ML.md) | Where ML frameworks are used to improve research quality |
| [docs/MODELS.md](docs/MODELS.md) | Model tiers, candidates, memory budget, bake-off protocol |
| [docs/WORKFLOW.md](docs/WORKFLOW.md) | Commit, documentation and testing conventions |
| [docs/decisions/](docs/decisions/README.md) | Architecture decision records (ADRs) |

## Licence

Not yet chosen.
