# 0002: Backend-agnostic inference over an OpenAI-compatible API

Date: 2026-11-23
Status: Accepted. Default backend chosen 2026-11-23, see Outcome.

## Context

Several runtimes serve local models on Apple Silicon: `mlx-lm` (Apple's MLX,
loads models directly from Hugging Face), Ollama (already installed here;
recent versions are reported to include an MLX engine alongside llama.cpp),
LM Studio, and llama.cpp. Public benchmarks disagree about which is fastest
and which handles structured output best, and the answer changes with each
release. All of them expose an OpenAI-compatible HTTP API.

## Decision

1. Mai talks to models only through an OpenAI-compatible chat completions
   endpoint, configured by base URL and model name per tier. No runtime is
   imported as a library by the pipeline.
2. Mai relies on JSON-schema structured output, not on native tool calling,
   which is the least consistent feature across runtimes.
3. Two backends are supported and documented from M1: **mlx-lm server**
   (direct Hugging Face MLX models) and **Ollama** (already installed).
4. The default is whichever wins the M1 bake-off on this machine, measured
   with the same models. The result is recorded in
   [MODELS.md](../MODELS.md).

## Alternatives considered

- **Load models in-process with mlx-lm**: slightly less overhead, but ties
  the pipeline to one runtime, makes model lifecycle part of the app, and
  blocks swapping backends.
- **Commit to one runtime now**: no measured basis for the choice.
- **LM Studio**: good performance reported, but GUI-centred and harder to
  script and document reproducibly. Works anyway, since it speaks the same
  API.

## Consequences

- Swapping runtime or model is a config change.
- The server is a separate process the user must start; `mai doctor` checks
  it and the setup guide covers it.
- Small task models (embedder, reranker, verifier) are the exception: they are
  loaded in-process through sentence-transformers / transformers, because
  they are tiny and are called in tight batches.

## Outcome (2026-11-23)

The M1 bake-off ([MODELS.md](../MODELS.md)) settled the default as **Ollama
with the model's MLX build**:

- The MLX build of Qwen 3.8 27B generated about 3.6 times faster than the
  llama.cpp (GGUF) build of the same model, at the same accuracy.
- Ollama 0.40.17's MLX runner cannot enforce a JSON schema; it answers
  `501 structured output is unavailable`. Its llama.cpp runner can.
- Because of that, decision 2 was strengthened: the client always states the
  schema in the prompt, sends `response_format` only to backends that accept
  it, validates every reply and makes one repair attempt. With prompt-only
  JSON the model was valid first time in 92 % of calls and in 100 % after
  the repair turn.

`mlx_lm.server` has not been tested yet. It stays a supported target in
principle (same API), and is worth testing because it might combine MLX speed
with schema enforcement.
