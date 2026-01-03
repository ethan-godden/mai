# Workflow

How work on Mai is carried out. These rules apply to people and to AI coding
agents alike.

## Commits

- **One commit per completed step.** Each numbered step in
  [PLAN.md](PLAN.md) ends with at least one commit. Never batch several steps
  into one commit.
- Commit messages use the imperative mood with a short subject line
  (max 72 characters), a blank line, then a body explaining *why*.
- Prefix the subject with the area: `docs:`, `feat:`, `fix:`, `test:`,
  `chore:`, `eval:`.
- A step is only committed when its checks pass (tests, lint). If something is
  knowingly left broken, the commit body says so.
- Models, caches, indexes, research runs and secrets are never committed
  (see `.gitignore`).

## Documentation

Documentation ships with the change, not after it.

| When you... | Update... |
|---|---|
| Finish a step or milestone | Status table in [PLAN.md](PLAN.md) |
| Add or change a component | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Make a choice that is expensive to reverse | A new ADR in [decisions/](decisions/README.md) |
| Add, swap or re-benchmark a model | [MODELS.md](MODELS.md) |
| Add or change an ML component | [ML.md](ML.md) |
| Change how the tool is installed or run | [README.md](../README.md) and the user guide (from M1) |

Rules of thumb:

- Write down measured numbers with the date, machine and command that produced
  them. Do not record numbers from memory or from blog posts as facts.
- ADRs are append-only. To change a decision, add a new ADR that supersedes
  the old one and mark the old one `Superseded by NNNN`.
- Public functions and modules get docstrings that explain intent. Comments
  explain *why*, not *what*.

## Testing

- Unit tests run offline and without a model: LLM, search and fetch are
  behind interfaces and are faked in tests.
- Integration tests that need a running model server or network are marked and
  skipped by default.
- Research *quality* is measured by the evaluation harness (milestone M7), not
  by unit tests.

## Tooling

- Python 3.12, managed with `uv` (see [ADR 0001](decisions/0001-python-and-uv.md)).
- `ruff` for lint and formatting, `pytest` for tests, `pyright` for types.
