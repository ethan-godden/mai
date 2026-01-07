# 0005: Dedicated ML models for retrieval and verification

Date: 2026-11-23
Status: Accepted

## Context

A stated project requirement is to use ML frameworks wherever they improve
research quality. The parts of research that most affect quality (choosing
what the model reads, and checking what it writes) are retrieval and
classification problems for which small specialised models outperform, and are
far cheaper than, a general LLM.

## Decision

1. Use established ML frameworks rather than prompt-only or hand-rolled
   approaches for: embeddings and hybrid retrieval, cross-encoder reranking,
   deduplication and diversity, source scoring, entailment-based citation
   verification, contradiction detection, routing, and prompt optimisation.
   The mapping is in [ML.md](../ML.md).
2. Frameworks: MLX / mlx-lm, Hugging Face transformers,
   sentence-transformers, scikit-learn, LanceDB, DSPy.
3. Every ML component is introduced behind an interface with a simple
   baseline, and is kept only if it improves the evaluation metrics.

## Alternatives considered

- **LLM for everything**: simplest code, but slow, expensive in context, and
  gives no independent check on citations.
- **Hosted embedding / rerank APIs**: violates the local-first goal.
- **Heavier RAG frameworks (LlamaIndex, Haystack)**: bundle many of these
  parts, but obscure the pieces this project wants to measure and tune
  individually. Can be revisited if hand-assembly proves costly.

## Consequences

- Better retrieval and a measurable citation-precision metric.
- More models to download, load and benchmark; a few GB of extra memory.
- Requires the evaluation harness (M7) to justify each component; a seed
  evaluation set is therefore started in M4.
