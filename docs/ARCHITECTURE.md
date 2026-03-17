# Architecture

Last updated: 2026-11-23. This describes the *planned* design. Sections are
updated as each milestone lands. Built so far (M1 to M3): `mai.cli`, `mai.config`,
`mai.llm`, `mai.doctor`, `mai.bakeoff`, `mai.search`, `mai.fetch`,
`mai.extract`, `mai.render`, `mai.read`, `mai.rewrite`, `mai.passages`,
`mai.quick`, `mai.runs` and `mai.prompts`. Everything else is planned.

## 1. Overview

Mai is a pipeline of small, replaceable stages. A local LLM is used only where
language understanding is needed; retrieval and checking are done by dedicated
ML models.

```
                         ┌──────────────────────────────┐
   CLI / MCP / Web UI ──▶│        Orchestrator          │──▶ runs/<id>/ (trace)
                         └──────────────┬───────────────┘
                                        │
     ┌──────────┬──────────┬────────────┼───────────┬────────────┬──────────┐
     ▼          ▼          ▼            ▼           ▼            ▼          ▼
  Planner    Search     Fetch &     Retrieval    Reader      Writer     Verifier
  (LLM)     providers   extract    (embed, BM25, (LLM,       (LLM)     (entailment
                                    rerank,      notes)                  model)
                                    cluster)
     │          │          │            │           │            │          │
     └──────────┴──────────┴─────┬──────┴───────────┴────────────┴──────────┘
                                 ▼
         Local services: inference server (OpenAI-compatible), SearXNG,
         vector store, page cache
```

## 2. Pipelines

### Quick mode (M3, built)

1. **Rewrite**: fast model turns the question into 2 to 4 search queries.
2. **Search**: queries run in parallel across providers; results are merged.
3. **Fetch**: top results are downloaded and extracted to Markdown.
4. **Retrieve**: pages are split into passages and the best are selected
   (BM25 keyword ranking now, the full ML stack in M4), with a cap per
   source.
5. **Answer**: the model writes a short answer from those passages only, with
   numbered citations. The fast tier is used, because thinking doubled the
   time.
6. **Check and record**: citation numbers are validated and the run is saved.

Reading is bounded by a time budget, so one slow site cannot hold up the
answer. A page that could not be read contributes its search snippet,
labelled as such. With no readable source the model is not called at all.
See [guide/usage.md](guide/usage.md).

### Deep mode (M5, M6)

1. **Plan**: main model produces sub-questions and a report outline.
2. **Research loop**, per sub-question, bounded by a budget:
   search → fetch → index → retrieve → **read** (fast model writes structured
   notes: claim, verbatim quote, source id).
3. **Gap analysis**: main model reviews notes against the outline and either
   issues follow-up queries or declares the research sufficient.
4. **Synthesise**: main model writes each section from the notes only. It
   never sees raw pages and may only cite note ids.
5. **Verify**: each claim is checked against its cited quote by an entailment
   model; unsupported claims are repaired or removed.
6. **Render**: Markdown report, source list, verification summary.

Writing from notes rather than raw pages keeps the context small, makes every
sentence traceable to a quote, and gives the verifier something precise to
check.

### Reading a source (M2, built)

```
query ─▶ providers in parallel ─▶ merge (reciprocal rank fusion) ─▶ ranked URLs
URL ─▶ safety check ─▶ cache ─▶ robots.txt ─▶ download ─▶ extract ─▶ Document
                                                  └─ no text? render in browser ─┘
```

A `Document` is the unit the rest of the pipeline works with: final URL,
title, Markdown text, author, publication date and site. Details are in
[guide/search.md](guide/search.md) and [guide/reading.md](guide/reading.md).

## 3. Components

| Component | Responsibility | Main dependencies |
|---|---|---|
| `mai.cli` | Commands: `ask`, `doctor`, `search`, `fetch` (built); `research`, `eval` (planned) | typer, rich |
| `mai.prompts` | Prompt templates as Markdown files | |
| `mai.rewrite` | Question to search queries | |
| `mai.passages` | Chunking and passage selection | rank-bm25 |
| `mai.quick` | Quick-mode orchestration | asyncio |
| `mai.config` | Typed settings from `config.toml` and environment | pydantic-settings |
| `mai.llm` | OpenAI-compatible client, structured output with prompt fallback and repair, model tiers, accounting | httpx, pydantic |
| `mai.doctor` | Environment checks behind `mai doctor` | httpx |
| `mai.bakeoff` | Model comparison battery and scoring | pydantic |
| `mai.search` | Provider interface, URL canonicalisation and rank-fusion merging; SearXNG, arXiv, Wikipedia, Semantic Scholar | httpx |
| `mai.fetch` | Concurrent fetching, disk cache, robots.txt, per-host pacing, private-address blocking | httpx |
| `mai.render` | Headless-browser rendering of JavaScript-only pages (optional) | playwright |
| `mai.read` | Fetch plus extract, with the browser fallback; what the pipeline calls | |
| `mai.extract` | HTML and PDF to Markdown plus metadata | trafilatura, pypdfium2 |
| `mai.retrieval` | Chunking, embeddings, hybrid search, reranking, dedup, diversity | sentence-transformers / MLX, LanceDB, scikit-learn |
| `mai.deep` | Deep-mode orchestration, budgets, resumability (planned) | asyncio |
| `mai.verify` | Claim splitting and entailment checking | transformers |
| `mai.report` | Rendering, citations, export | jinja2 |
| `mai.runs` | Run records: report, run data and model-call trace as plain files | |
| `mai.eval` | Evaluation set, metrics, DSPy optimisation | dspy |
| `mai.mcp` | MCP server exposing research tools | mcp SDK |
| `mai.web` | Local web UI | FastAPI |

Every stage that touches a model, the network or the disk sits behind a small
interface (a `Protocol`), so tests can substitute fakes and implementations
can be swapped without touching the pipeline.

## 4. Data model

```
Run          id, question, mode, config snapshot, started, finished, status
Query        run_id, text, provider, sub_question
Source       id, url, title, author, published, fetched_at, content_hash, quality
Chunk        source_id, position, text, embedding
Note         id, run_id, sub_question, claim, quote, source_id, chunk_position
Claim        report sentence, cited note ids, verdict, score
Report       run_id, markdown, sources, verification summary
```

A run is stored as a directory, `runs/<timestamp>-<slug>/`, containing
`run.json`, `report.md` and `trace.jsonl` (every model call with prompt,
output, timing and token counts); deep mode will add `notes.jsonl`. Runs are plain files so they
can be read, diffed and used as evaluation or fine-tuning data.

## 5. Models and memory

Three LLM tiers plus three small task models, all loaded locally. Details and
the memory budget are in [MODELS.md](MODELS.md).

| Role | Used for | Size class |
|---|---|---|
| Fast | Query rewriting, note-taking, claim splitting | small |
| Main | Planning, gap analysis, synthesis | medium |
| Heavy (optional) | Final synthesis of long reports | large |
| Embedder | Dense retrieval | tiny |
| Reranker | Passage reranking | tiny |
| Verifier | Claim / evidence entailment | tiny |

## 6. Local services

| Service | Purpose | Default address |
|---|---|---|
| Inference server | Serves LLMs over an OpenAI-compatible API | `http://localhost:11434/v1` (Ollama) or `:8080/v1` (mlx-lm) |
| SearXNG | Metasearch over public engines | `http://localhost:8888` |

Both are optional at import time: `mai doctor` reports what is missing and
how to start it.

## 7. Repository layout (planned)

```
Mai/
├── README.md
├── CLAUDE.md
├── pyproject.toml
├── config.example.toml
├── docs/
│   ├── PLAN.md  ARCHITECTURE.md  ML.md  MODELS.md  WORKFLOW.md
│   ├── decisions/          ADRs
│   └── guide/              setup, search, reading, usage, evaluation, mcp, web-ui
├── src/mai/                packages listed in section 3 (prompts in src/mai/prompts/)
├── tests/                  unit (offline) and integration (marked)
├── evals/                  question sets, metrics, results history
└── scripts/                bake-off, SearXNG setup, maintenance
```

Git-ignored at runtime: `models/`, `data/` (cache, vector store), `runs/`.

## 8. Cross-cutting concerns

- **Privacy**: questions go to search engines as queries; page fetches go to
  the sites themselves. Nothing else leaves the machine. No telemetry.
- **Politeness and safety**: robots.txt is respected, requests are paced per
  host and identified honestly, and private or local addresses are never
  fetched ([ADR 0006](decisions/0006-fetching-untrusted-pages.md)).
- **Untrusted content**: fetched pages are data, never instructions. Page text
  is delimited in prompts, and the pipeline's fixed structure means a page
  cannot trigger actions.
- **Budgets**: every run has limits on iterations, sources, tokens and wall
  time, set in config and overridable per command.
- **Failure handling**: a failed fetch or extraction is logged and skipped;
  the report lists sources that could not be read.
