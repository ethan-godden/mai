# ML strategy

Last updated: 2026-11-23

A research agent that only prompts a large language model is slow and
unreliable: the model reads too much irrelevant text and nothing checks what
it writes. Mai uses established ML frameworks and small purpose-built models
for the parts of research that are really retrieval, ranking, clustering or
classification problems. The LLM is kept for planning, reading and writing.

**Rule:** when a step can be framed as retrieval, ranking, clustering or
classification, use a dedicated model or library first, and measure it against
the prompt-only baseline before keeping it.

## 1. Frameworks

| Framework | Role in Mai |
|---|---|
| **MLX / mlx-lm** | Native Apple Silicon inference for LLMs pulled from Hugging Face; LoRA fine-tuning (M9) |
| **Hugging Face Hub, transformers** | Model distribution; loading the verifier and other task models |
| **sentence-transformers** (PyTorch, MPS) | Embedding models and cross-encoder rerankers |
| **scikit-learn** | Clustering, near-duplicate detection, diversity selection, simple learned scorers |
| **LanceDB** | Embedded vector store with hybrid (dense + full-text) search |
| **DSPy** | Programmatic prompt optimisation against the evaluation metrics |
| **pytest + the eval harness** | Regression testing of quality metrics |

Where a model exists in both PyTorch and MLX form, the faster one on this
machine wins, decided by measurement.

## 2. Where ML improves each stage

| Stage | Prompt-only baseline | ML technique | Milestone |
|---|---|---|---|
| Passage retrieval | Stuff whole pages into context | Dense embeddings + BM25, fused with reciprocal rank fusion | M4 |
| Passage ranking | Keep the first N chunks | Cross-encoder reranker scoring (question, passage) pairs | M4 |
| Redundancy | LLM reads the same fact ten times | Embedding similarity to drop near-duplicates; MMR for diverse selection | M4 |
| Coverage | Hope the queries were good | Cluster retrieved passages by topic; detect outline sections with thin evidence and target them in the next loop | M4, M5 |
| Source quality | Trust every page equally | Feature-based scorer: domain type, recency, corroboration across independent sources; later a learned classifier trained on run feedback | M4, M7 |
| Citation checking | Ask the LLM "is this right?" | Entailment (NLI / fact-checking) model scoring each claim against its cited quote | M6 |
| Contradictions | Unnoticed | Entailment model run across notes on the same sub-question to flag conflicting sources for the writer | M6 |
| Prompt quality | Hand-tuned prompts | DSPy optimisers tuning instructions and few-shot examples against eval metrics | M7 |
| Routing | Always use the biggest model | Lightweight classifier (embedding + logistic regression) choosing quick vs deep mode and model tier from the question | M7 |
| Model specialisation | Generic model | LoRA fine-tune of the note-taking model on accepted run traces | M9 |

## 3. Retrieval stack (M4)

```
pages ─▶ chunk ─▶ embed ─▶ vector store ─┐
                    BM25 index ──────────┤
                                         ▼
                 question ─▶ hybrid search (top ~100)
                                         ▼
                             cross-encoder rerank (top ~20)
                                         ▼
                       dedup + MMR diversity (top ~8 to 12)
                                         ▼
                                   reader model
```

- Chunking is structure-aware (headings, paragraphs), around 300 to 500
  tokens with a small overlap; exact sizes are tuned in M4.
- Embeddings are cached by content hash, so re-fetching a page is free.
- The index is per run by default, with an optional persistent cross-run
  index (M9).

## 4. Verification stack (M6)

1. The fast LLM splits the draft into atomic claims, each with its citations.
2. An entailment model scores (cited quote → claim). Scores map to
   *supported*, *partially supported*, *unsupported*.
3. Unsupported claims trigger one repair attempt: retrieve again from indexed
   passages, then rewrite or remove.
4. The report footer shows the totals; unsupported claims never ship silently.

A dedicated entailment model is used instead of the writer LLM because it is
much faster, is not biased toward its own text, and yields a calibrated score
that can be tracked as a metric.

## 5. Evaluation (M7)

| Metric | How it is computed |
|---|---|
| Citation precision | Share of claims the verifier marks supported, spot-checked by hand |
| Fact recall | Share of reference facts per question found in the report |
| Retrieval nDCG / recall@k | Against labelled relevant passages for the seed set |
| Source diversity | Number of independent domains cited |
| Rubric score | Local judge model on completeness, structure, clarity |
| Cost | Wall time, tokens generated, peak memory |

Every ML component above must show a gain on these metrics over the simpler
baseline to stay in the pipeline. Results, with date and command, are recorded
in this file under a "Measured results" section as they arrive.

## 6. Candidate task models

To be confirmed by measurement in M4 and M6. None are chosen yet.

| Task | Candidates |
|---|---|
| Embedding | Qwen3-Embedding (0.6B), BGE-M3, Nomic Embed |
| Reranking | BGE reranker v2 (m3), Qwen3-Reranker (0.6B) |
| Entailment / fact-checking | MiniCheck family, DeBERTa-v3 NLI models |

## 7. Measured results

None yet.
