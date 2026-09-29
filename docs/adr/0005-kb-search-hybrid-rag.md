# 0005. Knowledge base search: hybrid RAG (dense + BM25, RRF)

Status: Accepted · Date: 2026-09-26

## Context

`search_knowledge_base(query)` must find the right runbook section for how an operator or the LLM describes a problem. Queries mix two styles:

- exact tokens: service names, error codes, commands (`payments-api`, `5xx`, `ECONNRESET`),
- paraphrases: "checkout keeps failing" for a runbook titled "payments-api error rate".

Keyword search handles the first style, embeddings handle the second. Neither handles both.

## Options

| Option | Pros | Cons |
|---|---|---|
| BM25 only | Simple, exact matches, no model. | Misses paraphrases. |
| Dense only | Handles paraphrases. | Blurs exact names and codes. |
| Hybrid: dense + BM25, fused with RRF | Handles both styles. | Needs an embedding model and a vector store. |
| Hybrid + cross-encoder rerank | Best ranking. | Heavy model or paid API, slower. See [0006](0006-vector-store-qdrant-no-rerank.md). |

## Decision

Hybrid retrieval:

1. Runbooks (`data/kb/*.md`) are split into chunks by `##` section.
2. At query time, dense search (embedding cosine) and BM25 search run in parallel.
3. Results are fused with Reciprocal Rank Fusion: `score = Σ 1 / (k + rank)`, `k = 60`.
4. The tool returns the top 3 chunks with each retriever's rank, and the `mode` used (`hybrid` or `sparse_only`).

RRF uses ranks, not raw scores, so cosine and BM25 scores never need to be put on one scale.

## Consequences

- Robust to both query styles. The UI shows dense, BM25 and fused ranks for each hit.
- Needs Qdrant ([0006](0006-vector-store-qdrant-no-rerank.md)) and an embedding endpoint ([0007](0007-embeddings-api-sparse-fallback.md)).
- The choice is checked with numbers: the golden set ([0008](0008-evaluation-ragas-offline-online.md)) runs every question in `hybrid`, `dense` and `sparse` modes.

## Validation

Measured on 2026-09-28 with the golden set (`evals/kb_golden.jsonl`, 16 questions), the `text-embedding-bge-m3` embedding model in LM Studio, and the `kb` settings in `config.yaml` (`top_k_dense` 10, `top_k_bm25` 10, `rrf_k` 60, `top_n` 3). Report `fe58ab86`: no error rows, and every hybrid query used hybrid search.

| Mode | hit@3 | MRR@10 | recall@3 |
|---|---|---|---|
| hybrid | 1.00 | 0.97 | 1.00 |
| dense | 1.00 | 1.00 | 1.00 |
| sparse (BM25) | 0.94 | 0.89 | 0.94 |

Hybrid and dense find the right runbook in the top 3 for every question; BM25 alone misses one. On this set dense alone ranks slightly better than hybrid (MRR@10 1.00 against 0.97). The difference is one question, a paraphrase with no shared keywords ("The main Postgres node refuses all clients"): BM25's wrong first result puts the right runbook second in hybrid, where dense has it first. The case for hybrid, exact terms such as error codes, is not separated by this set: bge-m3 finds them too. `test_kb.py::test_exact_term_found_by_bm25` checks only that BM25 finds such a term and that hybrid keeps it first, with a word-overlap fake embedder. Hybrid also keeps search working as BM25 when embeddings fail ([0007](0007-embeddings-api-sparse-fallback.md)). The numbers do not change the decision, but they do not prove its benefit either; a golden set with more exact-term questions would.

RAGAS context precision and recall were not measured. The local judge model, `qwen/qwen3.5-9b`, is a reasoning model: it leaves the reply content empty, so RAGAS has nothing to parse ([DESIGN §8](../DESIGN.md#8-limitations)). A judged run of the 48 rows also takes about 1.5 h with it. To add the numbers, set a non-reasoning `JUDGE_MODEL` and run `cd backend && uv run python -m app.cli eval`.
