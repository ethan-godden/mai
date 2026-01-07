# 0001: Python 3.12 managed with uv

Date: 2026-11-23
Status: Accepted

## Context

The project depends on the ML ecosystem (MLX, transformers,
sentence-transformers, scikit-learn, DSPy), which is Python-first. The machine
has only the system Python 3.9.6, which is too old for these libraries, and
already has `uv` installed.

## Decision

Write Mai in Python 3.12. Use `uv` for the interpreter, virtual environment,
dependency locking and running tools. Use `ruff`, `pytest` and `pyright`.

## Alternatives considered

- **TypeScript / Node**: good for the web UI and MCP, but would need a Python
  sidecar for every ML component.
- **System Python + pip**: 3.9 is unsupported by current ML libraries and
  gives no lock file.
- **conda**: heavier, slower, and not needed since wheels exist for Apple
  Silicon.

## Consequences

- One language across pipeline, ML, evaluation and servers.
- `uv.lock` is committed so installs are reproducible.
- Python 3.12 rather than the newest release, to stay inside the version range
  ML wheels reliably support.
