# 0007. Embeddings via API, BM25-only fallback

Status: Accepted · Date: 2026-09-26

## Context

Dense search needs an embedding model. The knowledge base is in English.

## Options

| Option | Pros | Cons |
|---|---|---|
| OpenAI-compatible `/embeddings` API (LM Studio `bge-m3`, OpenAI `text-embedding-3-small`) | Works with local (LM Studio) and cloud providers. Switch provider with env vars. | Needs a running endpoint. |
| `fastembed` local ONNX model | Works offline, no server. | Model download on first start. New code path. |
| Both | Flexible. | Extra config branch and tests. |

## Decision

- Use an OpenAI-compatible `/embeddings` endpoint (`EMBED_*` env vars; empty values reuse `LLM_*`).
- If embedding fails at query time, the search runs BM25 only and returns `mode: "sparse_only"`. The UI highlights this.
- If embeddings are unavailable at ingest time, only BM25 vectors are indexed and `/api/health` reports the knowledge base as `sparse_only`. Run ingest again when the endpoint is back.

## Consequences

- The tool keeps working without an embedding endpoint, with weaker results for paraphrased queries.
- Changing `EMBED_MODEL` changes the vector space. Ingest rebuilds the collection when the hash of (documents + model name) changes.
