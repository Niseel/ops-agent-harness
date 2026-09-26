# 0006. Vector store: Qdrant server, no reranker

Status: Accepted · Date: 2026-09-26

## Context

Hybrid search ([0005](0005-kb-search-hybrid-rag.md)) needs a store for dense vectors and BM25 sparse vectors.

## Options

| Option | Pros | Cons |
|---|---|---|
| Qdrant server (Docker) | Dense and sparse vectors in one collection. Qdrant applies BM25 IDF at query time. Production-like. | Reviewers need Docker. |
| Qdrant local (embedded) mode | No Docker. Same client API. | Not how it runs in production. |
| SQLite with `sqlite-vec` + FTS5 | Everything in one file. | New code. Loading SQLite extensions is blocked on some Python builds. |
| In-memory NumPy + own BM25 | No dependencies. | New code; does not show a real retrieval setup. |

Reranker: a cross-encoder after RRF improves ordering, but it needs a ~2 GB model (torch) or a paid API, and adds latency against a 5 s tool timeout.

## Decision

- Qdrant server through `docker compose`.
- One collection, `ops_kb`, one point per chunk:
  - vectors: `dense` (cosine) and `bm25` (sparse, `Modifier.IDF`),
  - payload: `doc_id, title, section, text, content_hash, embed_model`.
- No reranker. The knowledge base is small (about 30 chunks), and the top 3 after RRF is enough.

## Consequences

- Both retrievers read one store, so there is no second index to keep in sync.
- If Qdrant is down, the tool returns a retryable `unavailable` error. After retries the agent continues without the knowledge base.
- Tests use `qdrant-client` in-memory mode, so they do not need Docker.
- Revisit reranking if the golden set shows good recall but poor order.
