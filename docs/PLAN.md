# Project plan

Last updated: 2026-11-23

## 1. Goal

Build a research agent that answers open-ended questions with cited,
verifiable reports, running entirely on local open-weight models, so that
research no longer consumes tokens on hosted services such as Claude.

### Success criteria

1. A question typed at the command line produces a Markdown report in which
   every factual claim carries a citation to a fetched source.
2. No hosted LLM API is called. Only search and page fetches leave the machine.
3. Quick mode answers in under 60 seconds; deep mode finishes a typical
   question in under 15 minutes, on the M3 Max / 128 GB target.
4. Citation precision (share of cited claims actually supported by the cited
   passage) is measured by the evaluation harness and is at least 90 %.
5. Claude Code can delegate research to Mai through an MCP tool, so the hosted
   model spends tokens only on reading the final report.
6. A new user can install and run Mai from the documentation alone.

### Scope

In scope: open-ended web research across any topic (technical, scientific,
market, general knowledge, how-to, comparisons), PDF and HTML sources, a CLI,
an MCP server, and a small local web UI.

Out of scope for now: sources behind logins or paywalls, multi-user hosting,
image/video understanding, training models from scratch.

## 2. Guiding principles

- **Local first.** Inference never leaves the machine.
- **Workflow before autonomy.** The research loop is a fixed, inspectable
  pipeline with structured (JSON-schema) model outputs, not a free-form agent.
  Smaller local models are far more reliable this way
  ([ADR 0004](decisions/0004-pipeline-over-agent-framework.md)).
- **Use ML where it beats prompting.** Retrieval, reranking, deduplication and
  citation checking are done by purpose-built models and ML libraries rather
  than by asking the LLM ([ML.md](ML.md)).
- **Measure, then choose.** Models and prompts are picked by benchmark on this
  machine, not by reputation ([MODELS.md](MODELS.md)).
- **Everything is traceable.** Each run saves its plan, queries, sources,
  notes and verification results to disk.
- **Documentation ships with the code** ([WORKFLOW.md](WORKFLOW.md)).

## 3. Milestones

Each step ends with a commit. Each milestone ends with the docs listed under
"Docs" being current.

### Status

| Milestone | Title | Status |
|---|---|---|
| M0 | Planning | Done |
| M1 | Foundations and model bake-off | Not started |
| M2 | Search, fetch and extract | Not started |
| M3 | Quick mode end to end | Not started |
| M4 | ML retrieval layer | Not started |
| M5 | Deep mode | Not started |
| M6 | Citation verification | Not started |
| M7 | Evaluation and prompt optimisation | Not started |
| M8 | Interfaces: MCP server and web UI | Not started |
| M9 | Stretch goals | Not started |

### M0: Planning

1. Initialise repository, README, workflow conventions.
2. Write plan and architecture.
3. Write ML strategy, model plan and decision records.

### M1: Foundations and model bake-off

Purpose: a working project skeleton and an evidence-based choice of models.

1. Create the `uv` project (Python 3.12), package layout, `ruff`, `pytest`,
   `pyright`, a `mai` CLI entry point that prints its version.
2. Configuration: typed settings loaded from `config.toml` plus environment
   overrides; `mai doctor` reports backend, models and memory.
3. LLM client: thin wrapper over an OpenAI-compatible endpoint with streaming,
   JSON-schema structured output, retries, and token/time accounting.
4. Backend setup: install and document the inference server
   ([ADR 0002](decisions/0002-inference-backend.md)); download candidate
   models from Hugging Face.
5. Bake-off script: run every candidate through the tasks in
   [MODELS.md](MODELS.md) and write a results table.
6. Choose the models for each tier and record the choice.

Exit criteria: `mai doctor` passes; `mai ask "..."` returns a plain (uncited)
local completion; bake-off results are committed in MODELS.md.

Docs: README (install), MODELS.md (results), new `docs/guide/setup.md`.

### M2: Search, fetch and extract

Purpose: turn a query into clean, attributed text.

1. Search provider interface plus a SearXNG implementation
   ([ADR 0003](decisions/0003-search-backend.md)); document how to run it.
2. Specialist providers: arXiv, Wikipedia, Semantic Scholar (keyless APIs).
3. Fetcher: concurrent `httpx` with per-host rate limits, timeouts, robots.txt
   respect, on-disk cache.
4. Extraction: `trafilatura` for HTML, a PDF extractor for papers, metadata
   (title, author, date) capture.
5. Headless-browser fallback for JavaScript-rendered pages.

Exit criteria: `mai search "..."` lists results; `mai fetch <url>` prints
clean Markdown with metadata; all covered by offline tests using recorded
fixtures.

Docs: ARCHITECTURE.md (search/fetch), `docs/guide/search.md`.

### M3: Quick mode end to end

Purpose: the first useful product, a Perplexity-style quick answer.

1. Query rewriting: the fast model turns the question into 2 to 4 search
   queries.
2. Source selection and passage selection (simple BM25 at this stage).
3. Answer synthesis with numbered inline citations, streamed to the terminal.
4. Run record: save question, queries, sources and answer under `runs/`.

Exit criteria: `mai ask "..."` streams a cited answer in under 60 s.

Docs: `docs/guide/usage.md`, ARCHITECTURE.md (quick pipeline).

### M4: ML retrieval layer

Purpose: make the model read the *right* passages. See [ML.md](ML.md).

1. Seed evaluation set: about 15 questions with labelled relevant passages,
   and a retrieval-metrics script (grown into the full harness in M7).
2. Chunking and local embedding model; vector store.
3. Hybrid retrieval (BM25 + dense) with reciprocal rank fusion.
4. Cross-encoder reranker.
5. Near-duplicate removal and diversity selection (clustering / MMR).
6. Source quality signals (domain type, recency, corroboration count).

Exit criteria: retrieval metrics on the evaluation seed set beat the M3 BM25
baseline; results recorded.

Docs: ML.md (measured results), ARCHITECTURE.md (retrieval).

### M5: Deep mode

Purpose: multi-step research on open-ended questions.

1. Planner: decompose the question into sub-questions and a report outline.
2. Research loop: for each sub-question, search, retrieve, and take
   structured notes (claim, supporting quote, source).
3. Gap analysis: decide what is still unknown and issue follow-up queries,
   within a budget of iterations, sources and time.
4. Synthesis: write the report section by section from notes only.
5. Progress display and resumable runs.

Exit criteria: `mai research "..."` produces a multi-section cited report in
under 15 minutes; a run can be resumed after interruption.

Docs: ARCHITECTURE.md (deep pipeline), usage guide.

### M6: Citation verification

Purpose: catch unsupported claims before the user reads them.

1. Split the report into atomic claims with their citations.
2. Check each claim against its cited passage with an entailment model.
3. Repair loop: re-retrieve or rewrite unsupported claims; drop what cannot be
   supported.
4. Report footer: verification summary and per-claim confidence.

Exit criteria: citation precision at least 90 % on the evaluation set.

Docs: ML.md (verifier), usage guide (how to read confidence marks).

### M7: Evaluation and prompt optimisation

Purpose: make quality measurable and improve it systematically.

1. Evaluation set: 40 to 60 questions across domains, with reference facts.
2. Metrics: citation precision, fact recall, source diversity, latency,
   tokens; a local judge model for rubric scoring.
3. `mai eval` command and a results history file.
4. Prompt optimisation with DSPy against the metrics.
5. Regression gate: a change may not lower headline metrics unnoticed.

Exit criteria: one command reproduces the full evaluation; optimised prompts
show a measured gain.

Docs: `docs/guide/evaluation.md`, ML.md.

### M8: Interfaces

Purpose: make Mai the default research path, including from Claude.

1. MCP server exposing `quick_answer` and `deep_research` tools, so Claude
   Code and Claude Desktop can delegate research and receive only the report.
2. Local web UI: question box, live progress, report with clickable sources,
   run history.
3. Export: Markdown and PDF.

Exit criteria: Claude Code completes a research request through the MCP tool;
the web UI runs a deep research job end to end.

Docs: `docs/guide/mcp.md`, `docs/guide/web-ui.md`, README.

### M9: Stretch goals

- Research over a local document library (PDFs, notes).
- Cross-session memory of past research.
- LoRA fine-tuning of the note-taking model on accepted runs (`mlx-lm`).
- Scheduled / recurring research briefs.

## 4. Risks

| Risk | Mitigation |
|---|---|
| Local models hallucinate or mis-cite | Notes-only synthesis, entailment verification (M6), evaluation gate (M7) |
| Tool calling is unreliable on local models | Fixed pipeline with schema-constrained JSON instead of free-form tool use |
| Search engines block or rate-limit a self-hosted metasearch | Pluggable providers, caching, specialist keyless APIs, optional paid search key |
| Deep runs are too slow | Small fast model for bulk steps, concurrency, strict budgets, caching |
| Models and runtimes change monthly | Backend behind one OpenAI-compatible interface; repeatable bake-off |
| Memory pressure with several models loaded | Explicit memory budget per tier (MODELS.md); load/unload policy |
| Extraction fails on JS-heavy or paywalled pages | Browser fallback; skip and log; never fabricate content |

## 5. Decisions still open

These have a recommended default and can be changed without rework before
the milestone that depends on them.

| Decision | Default | Needed by |
|---|---|---|
| Which models fill each tier | Decided by the M1 bake-off | M1 |
| Vector store | LanceDB (embedded, no server) | M4 |
| Web UI stack | FastAPI + server-rendered pages with HTMX | M8 |
| Optional paid search fallback | None | M2 |
| Licence | Undecided | Before publishing |
