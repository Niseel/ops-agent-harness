# Architecture Decision Records

One file per decision. Each record says what problem we had, what we considered, what we chose and what it costs.

| # | Decision | Status |
|---|---|---|
| [0000](0000-record-architecture-decisions.md) | Record architecture decisions | Accepted |
| [0001](0001-backend-python-fastapi.md) | Backend: Python 3.12 + FastAPI + uv | Accepted |
| [0002](0002-agent-loop-langgraph-custom-nodes.md) | Agent loop: LangGraph engine, our own nodes | Accepted |
| [0003](0003-llm-openai-compatible-with-fake.md) | LLM: raw OpenAI SDK, any OpenAI-compatible provider, fake by default | Accepted (FakePlanner incident rule changed; reply wait added in M6) |
| [0004](0004-state-and-database-sqlite.md) | State and database design: SQLite | Accepted |
| [0005](0005-kb-search-hybrid-rag.md) | Knowledge base search: hybrid RAG (dense + BM25, RRF) | Accepted |
| [0006](0006-vector-store-qdrant-no-rerank.md) | Vector store: Qdrant server, no reranker | Accepted |
| [0007](0007-embeddings-api-sparse-fallback.md) | Embeddings via API, BM25-only fallback | Accepted |
| [0008](0008-evaluation-ragas-offline-online.md) | Evaluation: RAGAS offline (golden set) and online (per run) | Accepted (golden set path updated by 0018; online evaluation refined in M2) |
| [0009](0009-human-approval-interrupt.md) | Human approval before `create_incident` | Accepted |
| [0010](0010-failure-handling-envelope-retry-repair.md) | Failure handling: result envelope, selective retry, reply repair | Accepted (`max_repairs` clarified; LLM retries limited to transient errors) |
| [0011](0011-execution-limits.md) | Execution limits in layers | Accepted (`max_repairs` clarified) |
| [0012](0012-fault-injection-per-run.md) | Fault injection per run | Accepted (`embeddings` fault added) |
| [0013](0013-recovery-interrupted-runs.md) | Recovery: interrupted runs resume by hand | Accepted (decisions survive a crash, M3) |
| [0014](0014-observability-trace-events.md) | Observability: trace events + JSON logs | Accepted |
| [0015](0015-ui-run-console-not-chat.md) | UI concept: run console with approval inbox, not a chat | Accepted |
| [0016](0016-ui-angular.md) | UI tech: Angular | Accepted |
| [0017](0017-docs-postman-ci.md) | Docs in repo, Postman collection, CI | Accepted (plan doc replaced by 0018; Qdrant container serves the end-to-end job, M5) |
| [0018](0018-adopt-ai-sdlc-workflow.md) | Adopt the AI-SDLC workflow | Accepted (`/feature` adapted; saved traces hold the run's incidents, M5) |

## Format

```
# NNNN. Title
Status: Accepted | Superseded by NNNN · Date: YYYY-MM-DD

## Context        the problem and the forces behind it
## Options        what we considered, with pros and cons
## Decision       what we chose
## Consequences   what we gain, what we give up, when to revisit
```

## Rules

- Do not rewrite the decision of an accepted record. Adding evidence (for example a `Validation` section with measured results) is fine.
- To change a decision, add a new record that says `Supersedes NNNN`, and mark the old one `Superseded by NNNN`.
