# Models

Last updated: 2026-11-23

No model has been chosen yet. This document defines the tiers, the memory
budget, the candidates to test, and the bake-off that decides (milestone M1).

> The open-weight landscape changes monthly. The candidate list below comes
> from a web survey on 2026-11-23 of blog posts and vendor pages of mixed
> quality. Treat it as a starting list to test, not as a ranking. Re-check
> Hugging Face trending before running the bake-off.

## 1. Tiers

| Tier | Jobs | What matters | Size class |
|---|---|---|---|
| Fast | Query rewriting, note extraction, claim splitting | Speed, reliable JSON | ~4 to 12 B params |
| Main | Planning, gap analysis, synthesis | Reasoning, instruction following, long context | ~27 to 35 B params |
| Heavy (optional) | Final synthesis for long reports | Writing quality | ~70 to 120 B params |

## 2. Memory budget (128 GB unified memory)

Estimates for planning; real figures are measured in M1 and recorded here.

| Item | Estimate |
|---|---|
| macOS, apps, browser fallback | 16 to 24 GB |
| Fast model (4-bit) | 4 to 8 GB |
| Main model (4 to 6-bit) | 18 to 28 GB |
| Embedder + reranker + verifier | 2 to 4 GB |
| Context / KV caches | 5 to 15 GB |
| **Everyday total (fast + main + task models)** | **~30 to 55 GB** |
| Heavy model (4-bit, ~120 B) | 60 to 75 GB |

Policy: fast, main and the task models stay loaded together. The heavy model
does not fit comfortably alongside the main model with long contexts, so it is
loaded only for the final synthesis step and the main model is unloaded first.

## 3. Candidates to test

| Tier | Candidates | Notes |
|---|---|---|
| Fast | Gemma 4 (small), Qwen 3.x small variants, gpt-oss-20b | |
| Main | Qwen 3.5 27B, Qwen 3.6 35B-A3B, Gemma 4 (largest) | Qwen family is widely reported as the most reliable for structured / tool output |
| Heavy | gpt-oss-120b, largest GLM or Qwen variant that fits in ~70 GB | GLM-5.x is reported as the strongest open model, but may be too large at usable quantisation |

Prefer MLX builds (for example from the `mlx-community` organisation on
Hugging Face) where available; fall back to GGUF.

Sources consulted for this list are in section 6.

## 4. Bake-off protocol (M1)

Run by `scripts/bakeoff.py` against each candidate, on this machine, through
the same OpenAI-compatible client the pipeline uses.

| Task | Measures | Metric |
|---|---|---|
| Structured output | 50 schema-constrained JSON requests of varying complexity | Valid-schema rate |
| Query rewriting | 20 questions to search queries | Human-rated usefulness (1 to 5) |
| Note extraction | 20 passages with known facts | Fact recall, verbatim-quote accuracy |
| Grounded answering | 15 questions with supplied passages | Citation correctness, refusal when evidence is missing |
| Long-context synthesis | 5 note sets of ~20k tokens | Rubric score, citation correctness |
| Throughput | All of the above | Prompt tokens/s, generation tokens/s, time to first token |
| Memory | Peak resident memory while loaded and generating | GB |

Selection rule: for each tier, pick the fastest model that clears the quality
bar (valid-schema rate at least 98 %, citation correctness at least 90 %).
Record the full table below with date, runtime version and quantisation.

## 5. Results

None yet.

## 6. Sources for the candidate list

- [Best LLM models for a 128GB MacBook for agentic AI (bswen)](https://docs.bswen.com/blog/2026-03-21-best-llm-models-128gb-macbook-agentic-ai/)
- [Best MLX local LLMs on Apple Silicon in 2026 (bestllmfor.com)](https://bestllmfor.com/guides/mlx-local-llm-apple-silicon/)
- [Rapid-MLX project page (PyPI)](https://pypi.org/project/rapid-mlx)
- [The open weight models that matter, June 2026 (OpenRouter)](https://openrouter.ai/blog/insights/the-open-weight-models-that-matter-june-2026/)
- [Best open-weight LLMs in July 2026 (DEV)](https://dev.to/techlatestnet/best-open-weight-llms-in-july-2026-tested-compared-2f33)
- [Best open-weight LLMs 2026 (Context Studios)](https://www.contextstudios.ai/en/guides/best-open-weight-llms-2026)
