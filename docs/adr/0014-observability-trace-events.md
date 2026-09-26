# 0014. Observability: trace events + JSON logs

Status: Accepted · Date: 2026-09-26

## Context

Every execution needs a trace: what the LLM was asked, what it decided, each tool attempt, retries, approvals, limits, evaluation scores. The UI needs the same data live.

## Options

| Option | Pros | Cons |
|---|---|---|
| Own trace events in SQLite + SSE, JSON logs | No extra infrastructure. The UI shows exactly what ran. | No standard tracing backend. |
| OpenTelemetry + Jaeger | Standard spans and waterfall view. | Extra container and dependencies; overlaps with the UI. |
| Langfuse / LangSmith | LLM-focused tracing, token costs. | Needs an account and keys; hard for reviewers to reproduce. |

## Decision

- Every node, LLM call, tool attempt, retry, approval and evaluation emits an event:
  `{seq, run_id, t_ms, kind, node, tool, status, attention, msg, data}`.
- Events are appended to the `events` table and published to live subscribers.
- `GET /api/runs/{id}/events` (Server-Sent Events) replays stored events, then streams new ones. It supports `Last-Event-ID`.
- `GET /api/runs/{id}/trace` exports all events of a run as JSON.
- The same events are written to stdout as JSON log lines with `run_id`.
- `attention` (`info`, `warn`, `error`, `success`) marks what a human should notice. The UI highlights it.

## Consequences

- Reviewers see the trace in the UI, the API and the logs without installing anything.
- No distributed tracing. Events map one-to-one to spans, so an OpenTelemetry exporter is a small future change.
