# 0004: Fixed pipeline with structured output, no agent framework

Date: 2026-11-23
Status: Accepted

## Context

A research agent can be built as a free-form loop in which the model chooses
tools at each turn, usually on top of a framework such as LangChain/LangGraph,
or as a fixed workflow in which code controls the order of steps and the model
fills in typed outputs. Local models in the 10 to 35 B range are noticeably
less reliable than frontier hosted models at long free-form tool-use loops,
and tool-calling support differs between runtimes.

## Decision

Implement research as an explicit pipeline in plain Python (`asyncio`). Each
model call has one job and returns a Pydantic-validated JSON object. Iteration
(the deep-mode research loop) is a bounded loop in code, with the model
deciding only *what to search next* and *whether evidence is sufficient*.

No general agent framework is used for orchestration. DSPy is used, but only
for prompt optimisation and evaluation
([ADR 0005](0005-ml-components.md)).

## Alternatives considered

- **LangGraph / LangChain**: fast to start, but adds heavy abstractions,
  frequent breaking changes, and hides prompts that this project needs to
  measure and optimise.
- **Free-form tool-calling agent**: most flexible, least predictable on local
  models, hardest to evaluate and budget.
- **smolagents / code-writing agents**: powerful, but executing
  model-written code driven by untrusted web content is a risk not worth
  taking here.

## Consequences

- Behaviour is predictable, testable offline, and every step is traceable.
- Budgets (time, sources, iterations) are enforced in code.
- Web content cannot redirect the agent, because pages only ever fill data
  slots in fixed prompts.
- Less flexible for tasks that do not fit search-read-write; new capabilities
  are added as new pipeline stages or modes.
