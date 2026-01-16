# Setup

How to install Mai and get a local model answering. Verified on 2026-11-23 on
an M3 Max with 128 GB, macOS, Ollama 0.40.17, uv 0.13.9.

## 1. Prerequisites

| Tool | Why | Install |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | Python and dependency management | `brew install uv` |
| [Ollama](https://ollama.com) | Serves local models | `brew install ollama` |

You do not need to install Python yourself; uv fetches Python 3.12.

## 2. Install Mai

From the repository root:

```bash
uv sync
```

```bash
uv run mai --version
```

## 3. Start the model server and get a model

Mai talks to any server with an OpenAI-compatible API
([ADR 0002](../decisions/0002-inference-backend.md)). Ollama is the tested
default.

Start the server if it is not already running (the Ollama desktop app does
this for you):

```bash
ollama serve
```

Download a model. Which models to use is decided by the bake-off; see
[MODELS.md](../MODELS.md) for the current choice and results.

```bash
ollama pull qwen3.8:27b
```

Models are large (tens of GB each). `ollama list` shows what is installed and
`ollama ps` shows what is loaded in memory.

### Which build of a model?

Ollama serves the same model in two ways, and the difference matters:

| Build | Runner | Speed (see MODELS.md) | Schema-enforced JSON |
|---|---|---|---|
| Default tag, e.g. `qwen3.8:27b` | MLX | Faster | Not supported in Ollama 0.40.17 |
| GGUF tag, e.g. `qwen3.8:27b-q4_K_M` | llama.cpp | Slower | Supported |

`ollama show <model>` reports the format (`safetensors` means MLX, `gguf`
means llama.cpp). Mai works with both: when a server cannot enforce a JSON
schema, Mai states the schema in the prompt, validates the reply and retries
once if needed.

## 4. Configure

```bash
cp config.example.toml config.toml
```

Edit `config.toml` and set the model for the `fast` and `main` tiers. The
file is commented; the important lines are:

```toml
[llm.fast]
model = "qwen3.8:27b"
reasoning_effort = "none"   # skip thinking on simple steps

[llm.main]
model = "qwen3.8:27b"
```

Settings can also come from the environment, which overrides the file, for
example `MAI_LLM__MAIN__MODEL=qwen3.8:27b`. The config file is looked for at
`$MAI_CONFIG`, then `./config.toml`, then `~/.config/mai/config.toml`.

## 5. Check and try it

```bash
uv run mai doctor
```

`doctor` checks the machine, the config file, the data directories, that the
server is reachable and that each configured model is actually installed. It
exits non-zero and says how to fix anything that fails.

```bash
uv run mai ask "What is retrieval-augmented generation?"
```

Until milestone M3, `mai ask` is a plain model answer with no web search and
no citations.

## 6. Other servers

Any OpenAI-compatible server works by changing `base_url`:

| Server | `base_url` | Status |
|---|---|---|
| Ollama | `http://localhost:11434/v1` | Tested |
| mlx-lm (`mlx_lm.server`) | `http://localhost:8080/v1` | Not yet tested with Mai |
| LM Studio | `http://localhost:1234/v1` | Not yet tested with Mai |

A tier can use a different server from the others by giving it its own
`base_url`.

## 7. Development checks

```bash
uv run pytest
```

```bash
uv run ruff check . && uv run ruff format --check . && uv run pyright
```

Tests run offline with no model server. See [WORKFLOW.md](../WORKFLOW.md).

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Inference server ... unreachable` | Ollama is not running. Start it with `ollama serve` or open the app. |
| `Model: ... not found` | The name in `config.toml` does not match `ollama list`. Names include the tag after the colon. |
| `No model configured for the 'main' tier` | No `config.toml`, or `model` is empty. See step 4. |
| First answer is slow | The model is being loaded into memory. Later calls are fast until Ollama unloads it after a few idle minutes. |
| `Invalid configuration` | A key in `config.toml` is misspelt; unknown keys are rejected on purpose. |
