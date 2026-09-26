# 0004. State and database design: SQLite

Status: Accepted · Date: 2026-09-26

## Context

We need to store:

- run state (messages, counters) that survives a restart,
- approvals that may wait for minutes,
- an execution history and trace that the UI and API can query,
- evaluation results,
- the mock incident system.

## Options

| Option | Pros | Cons |
|---|---|---|
| SQLite, one file | No service to run. Transactions. Easy to query. Supported by the LangGraph checkpointer. | One writer at a time. |
| JSON files | Simplest. | No transactions, hard to query, races between approve and resume. |
| PostgreSQL + SQLAlchemy | Production-like, many workers. | Extra container, ORM and migrations for a single-process app. |

## Decision

One SQLite file, `data/harness.db`, plain SQL through `aiosqlite` (no ORM).

LangGraph's checkpointer owns `checkpoints` and `writes`. They are the source of truth for messages and counters. Our tables:

```
runs          id PK, objective, status, llm_mode, model, options_json, final, error,
              steps, tool_calls, created_at, updated_at, finished_at
approvals     id PK, run_id, tool_call_id, tool, args_json, status, decision_json,
              reason, decided_by, created_at, decided_at, expires_at
              UNIQUE(run_id, tool_call_id)
events        seq INTEGER PK AUTOINCREMENT, run_id, t_ms, kind, node, tool, status,
              attention, msg, data_json, created_at          INDEX(run_id, seq)
evals         id PK, run_id, target, metric, value, judge_model, error, created_at
eval_reports  id PK, created_at, models_json, config_json, summary_json, rows_json
incidents     id PK, idempotency_key UNIQUE, run_id, title, description, severity,
              status, created_at
```

Why each table:

- `runs`: one row per run, for lists and status. Run options (limits, faults, LLM mode) are stored here so a resumed run uses the same options.
- `approvals`: audit of human decisions. The unique key and a conditional update (`WHERE status = 'pending'`) stop double decisions.
- `events`: append-only trace. `seq` orders events and lets a client resume a live stream (`Last-Event-ID`). `attention` marks what a human should notice.
- `evals`, `eval_reports`: online per-run scores and offline golden-set reports ([0008](0008-evaluation-ragas-offline-online.md)).
- `incidents`: the mock external system. `idempotency_key` makes retries safe ([0009](0009-human-approval-interrupt.md)).

There is no `tool_calls` table: tool events already carry arguments, attempts, duration and result.

Run statuses: `running`, `awaiting_approval`, `completed`, `failed`, `limit_exceeded`, `timed_out`, `cancelled`, `interrupted`.

## Consequences

- Nothing to install; the whole state is one file.
- Only one process should write. To run several workers, move to PostgreSQL (`langgraph-checkpoint-postgres`, same SQL for our tables).
- The mock incidents live in the same file for simplicity. A real system would be a separate service behind the same tool interface.
