# Architecture decision records

Each record captures one decision that would be expensive to reverse: the
context, what was chosen, what was rejected and the consequences.

Records are append-only. To change a decision, add a new record and mark the
old one `Superseded by NNNN`.

| No. | Decision | Status |
|---|---|---|
| [0001](0001-python-and-uv.md) | Python 3.12 managed with uv | Accepted |
| [0002](0002-inference-backend.md) | Backend-agnostic inference over an OpenAI-compatible API | Accepted; default is Ollama with MLX builds |
| [0003](0003-search-backend.md) | Self-hosted SearXNG behind a provider interface | Accepted |
| [0004](0004-pipeline-over-agent-framework.md) | Fixed pipeline with structured output, no agent framework | Accepted |
| [0005](0005-ml-components.md) | Dedicated ML models for retrieval and verification | Accepted |

## Template

```markdown
# NNNN: Title

Date: YYYY-MM-DD
Status: Proposed | Accepted | Superseded by NNNN

## Context
## Decision
## Alternatives considered
## Consequences
```
