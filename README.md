# Mai

A local-first research agent. Mai answers open-ended research questions the way
Perplexity does (search the web, read the sources, write a cited answer) but
runs every model on this machine, using open-weight models from Hugging Face.

The goal is to move research work off paid, token-metered services such as
Claude and onto local hardware, without giving up cited, checkable answers.

**Status:** milestone M1 (foundations) in progress. Mai can check its
environment and answer a question with a local model. Web search and citations
arrive in M2 and M3. See the [roadmap](docs/PLAN.md).

## Quick start

Full instructions: [docs/guide/setup.md](docs/guide/setup.md).

```bash
uv sync
cp config.example.toml config.toml   # then set the model names
uv run mai doctor
uv run mai ask "What is retrieval-augmented generation?"
```

| Command | What it does |
|---|---|
| `mai doctor` | Checks machine, config, model server and models |
| `mai ask "..."` | Answers with a local model (no search or citations yet) |

## Target machine

| | |
|---|---|
| Hardware | MacBook Pro, Apple M3 Max, 128 GB unified memory |
| OS | macOS |
| Network | Needed for web search and page fetching only. All inference is local. |

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
| [docs/PLAN.md](docs/PLAN.md) | Goals, scope, milestones, exit criteria, risks |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Components, data flow, data model, repository layout |
| [docs/ML.md](docs/ML.md) | Where ML frameworks are used to improve research quality |
| [docs/MODELS.md](docs/MODELS.md) | Model tiers, candidates, memory budget, bake-off protocol |
| [docs/WORKFLOW.md](docs/WORKFLOW.md) | Commit, documentation and testing conventions |
| [docs/decisions/](docs/decisions/README.md) | Architecture decision records (ADRs) |

## Licence

Not yet chosen.
