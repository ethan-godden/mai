# Models

Last updated: 2026-11-23

## 1. Current choice

| Tier | Model (Ollama name) | Settings | Status |
|---|---|---|---|
| Fast | `qwen3.8:27b` | `reasoning_effort = "none"` | Provisional |
| Main | `qwen3.8:27b` | thinking on (default) | Provisional |
| Heavy | not set (falls back to main) | | Open |

Qwen 3.8 27B (Apache 2.0) in Ollama's MLX build is used for both tiers: one
model in memory, with reasoning switched off for the simple, high-volume
steps. It scored at or near 100 % on every bake-off task (section 5).

This is provisional because only two builds of one model have been tested so
far. It is good enough to build milestones M2 to M5 on. Section 6 lists what
is still worth comparing.

## 2. Tiers

| Tier | Jobs | What matters | Size class |
|---|---|---|---|
| Fast | Query rewriting, note extraction, claim splitting | Speed, reliable JSON | ~4 to 27 B params |
| Main | Planning, gap analysis, synthesis | Reasoning, instruction following, long context | ~27 to 35 B params |
| Heavy (optional) | Final synthesis for long reports | Writing quality | ~70 to 120 B params |

## 3. Memory budget (128 GB unified memory)

Measured figures are marked; the rest are planning estimates.

| Item | Memory |
|---|---|
| macOS, apps, browser fallback | 16 to 24 GB (estimate) |
| `qwen3.8:27b`, MLX build, loaded with 262k context | 34 GB (measured, `ollama ps`) |
| `qwen3.8:27b-q4_K_M`, llama.cpp build, same context | 24 GB (measured, `ollama ps`) |
| Embedder + reranker + verifier | 2 to 4 GB (estimate) |
| Heavy model (4-bit, ~120 B) | 60 to 75 GB (estimate) |

Using one model for the fast and main tiers costs 34 GB in total and leaves
room for a heavy model to be loaded alongside it.

## 4. Bake-off protocol

Implemented in `src/mai/bakeoff.py`, data in `evals/bakeoff/`, run with:

```bash
uv run python scripts/bakeoff.py MODEL [MODEL ...] [--reasoning-effort none] [--base-url URL]
```

Every request goes through the same client the pipeline uses, one at a time,
at temperature 0, after a warm-up call so load time is excluded. Passages
describe invented entities, so a model has to read them and cannot answer
from memory.

| Task | Cases | Metric | How it is scored |
|---|---|---|---|
| Structured output | 12 prompts over 4 schemas | JSON valid on first try; valid after one repair turn | Pydantic validation |
| Query rewriting | 8 questions | Valid; queries distinct | Automatic; usefulness is judged by reading the saved queries |
| Note extraction | 8 passages, 32 known facts | Fact recall; quotes verbatim | Keyword match; quote is a substring of the passage |
| Grounded answering | 8 answerable, 4 unanswerable | Answer correct; citation correct; declines when unsupported | Keyword match; cites the supporting passage and no other |
| Speed | all 40 calls | Median tokens/s after first token; median time to first token; total time | Client timing |
| Memory | | Loaded size | `ollama ps` |

Quality bar for a tier: JSON valid after repair at least 98 %, citation
correct at least 90 %, declines when unsupported at least 90 %. Among models
that clear the bar, prefer the faster.

Known limits of the battery:

- It is small and, as section 5 shows, too easy to separate strong models. It
  establishes that a model clears the bar and how fast it is; it does not
  rank models that all clear it.
- Long-context synthesis is not tested. It needs realistic note sets, which
  will exist once deep mode (M5) produces them.
- Scoring is by keyword. Scores are worth trusting only after reading the
  misses in the saved results file.

## 5. Results

Run on 2026-11-23, Apple M3 Max, 128 GB, macOS, Ollama 0.40.17. Raw outputs
are in `evals/bakeoff/results/`.

| Model | Runner | JSON valid 1st try | JSON valid | Fact recall | Quotes verbatim | Answer correct | Citation correct | Declines unsupported | Tok/s | TTFT | Total | Memory |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `qwen3.8:27b`, effort none | MLX (nvfp4) | 92% | 100% | 100% | 100% | 100% | 100% | 100% | 50.2 | 1.4 s | 216 s | 34 GB |
| `qwen3.8:27b-q4_K_M`, effort none | llama.cpp (Q4_K_M) | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 13.9 | 1.7 s | 596 s | 24 GB |
| `qwen3.8:27b`, thinking on | MLX (nvfp4) | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 33.4 | 1.5 s | 813 s | 34 GB |

What the results show:

- **Quality is at the ceiling for all three.** The battery cannot separate
  them on accuracy.
- **The MLX build is about 3.6 times faster** than the llama.cpp build of the
  same model (50 vs 14 tokens/s) and finished the battery in about a third of
  the time. The llama.cpp figure may be depressed by both builds being loaded
  at once; an isolated spot check earlier the same day measured 20 tokens/s.
  Either way MLX is clearly faster.
- **Ollama's MLX runner cannot enforce a JSON schema** (it answers
  `501 structured output is unavailable`), so Mai falls back to stating the
  schema in the prompt. That cost one repair turn in 40 calls (92 % valid on
  the first try, 100 % after repair). The llama.cpp runner enforces schemas
  and was valid first time, every time.
- **Thinking has a large time cost and showed no quality gain here.** With
  thinking on, the model generated 3.4 times as many tokens (24,415 vs 7,180)
  and took 3.8 times as long. That is why the fast tier turns it off. Whether
  thinking helps planning and synthesis is not something this battery can
  show, so the main tier keeps the default until the M7 evaluation can
  measure it.

Notes on the numbers:

- With thinking on, tokens/s is lower mainly because reasoning tokens are
  counted in the total while timing starts at the first reasoning token; it
  is not directly comparable with the other rows.
- `answer_correct` was recomputed from the saved outputs after a faulty
  answer key was fixed: question g2 wrongly rejected the bare answer "2". All
  three runs had given the correct answer.

## 6. Still to compare

These need downloads and have not been tested. Sizes are from the Hugging
Face API on 2026-11-23.

| Tier | Candidate | Size | Why |
|---|---|---|---|
| Fast | `mlx-community/gpt-oss-20b-MXFP4-Q8` | 11.3 GB | A smaller model could make the bulk steps several times faster |
| Main | `mlx-community/Qwen3.6-35B-A3B-4bit` | 19.0 GB | Mixture-of-experts design with few active parameters; possibly much faster at similar quality |
| Main | `mlx-community/Qwen3.8-27B-8bit` | 27.5 GB | Whether higher precision improves synthesis |
| Heavy | `mlx-community/gpt-oss-120b-MXFP4-Q8` | not yet checked | Largest widely used model likely to fit |

Also untested: `mlx_lm.server` as a backend, and whether it enforces JSON
schemas (which would give MLX speed and schema enforcement together).

A harder battery is needed before these comparisons mean much on quality;
until M7 they would mostly be speed comparisons.

## 7. Sources for the candidate list

- Hugging Face API listing of `mlx-community` models by downloads and trending, 2026-11-23
- [Best LLM models for a 128GB MacBook for agentic AI (bswen)](https://docs.bswen.com/blog/2026-03-21-best-llm-models-128gb-macbook-agentic-ai/)
- [Best MLX local LLMs on Apple Silicon in 2026 (bestllmfor.com)](https://bestllmfor.com/guides/mlx-local-llm-apple-silicon/)
- [The open weight models that matter, June 2026 (OpenRouter)](https://openrouter.ai/blog/insights/the-open-weight-models-that-matter-june-2026/)
- [Best open-weight LLMs in July 2026 (DEV)](https://dev.to/techlatestnet/best-open-weight-llms-in-july-2026-tested-compared-2f33)
