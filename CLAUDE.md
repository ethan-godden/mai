# Mai: guidance for AI coding agents

Mai is a local-first research agent (Perplexity-style) that runs open-weight
Hugging Face models on an M3 Max with 128 GB RAM.

Read before working:

- [docs/PLAN.md](docs/PLAN.md): the roadmap. Work the next unfinished step, in order.
- [docs/WORKFLOW.md](docs/WORKFLOW.md): commit and documentation rules.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): where things go.

Non-negotiables:

1. Commit after every completed step of the plan. One step, one commit (or more), never several steps in one commit.
2. Documentation is part of the step. Update the docs listed in WORKFLOW.md in the same commit series.
3. All model inference is local. Do not add calls to hosted LLM APIs.
4. Prefer an established ML framework (MLX, sentence-transformers, scikit-learn, DSPy) over hand-rolled heuristics where it measurably improves research quality. See [docs/ML.md](docs/ML.md).
5. Record measured results only, with date, machine and command.
