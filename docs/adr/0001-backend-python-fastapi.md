# 0001. Backend: Python 3.12 + FastAPI + uv

Status: Accepted · Date: 2026-09-26

## Context

The harness needs an HTTP API (for the UI and Postman), a CLI, async I/O (LLM calls, tool calls, live event streams) and strict validation of tool inputs and outputs.

## Options

| Option | Pros | Cons |
|---|---|---|
| Python + FastAPI | Async by default. Pydantic models validate data and produce JSON Schema. OpenAPI docs for free. Mature clients for LLMs, Qdrant and RAGAS. | One process; scaling out needs a worker queue. |
| TypeScript + Node (Hono/Fastify) | One language for backend and frontend. zod for schemas. | LLM evaluation tooling (RAGAS) is Python-first. |
| Python CLI only | Fastest to build. | No UI, no Postman collection. |

## Decision

Python 3.12, FastAPI, uv (with a lock file), pytest. The CLI uses `argparse` and calls the same core as the API.

## Consequences

- Pydantic tool models are used twice: to validate data and to generate the tool definitions sent to the LLM.
- `asyncio` gives us timeouts (`wait_for`, `timeout`) and cancellation without extra libraries.
- Everything runs in one process. See [0004](0004-state-and-database-sqlite.md) and [0013](0013-recovery-interrupted-runs.md) for what that means for scale and restarts.
