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

Pending. Filled in from the golden-set report (hit@3, MRR, context precision, context recall per mode) once search and evaluation are implemented.
