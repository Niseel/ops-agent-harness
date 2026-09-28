# 0013. Recovery: interrupted runs resume by hand

Status: Accepted · Date: 2026-09-26 · Refined in M3: a decision stored before a crash is applied on resume ([spec, Approval](../../specs/ops-agent-harness.md#approval))

## Context

The process can stop in the middle of a step. The checkpoint holds the last completed step, but a tool call may have been in flight when the process died.

## Options

| Option | Pros | Cons |
|---|---|---|
| Mark `interrupted`, resume by hand | A human decides. Safe with side effects. | Needs an action. |
| Resume automatically at startup | No action needed. | May repeat a tool call that already happened. The idempotency key only protects `create_incident`. |
| Mark `failed` | Simplest. | Throws away the checkpoint. |

## Decision

- At startup, every run with status `running` becomes `interrupted`.
- `POST /api/runs/{id}/resume` (or `cli resume <id>`) continues from the last checkpoint.
- Runs in `awaiting_approval` are not touched; their pause is already saved.

## Consequences

- No hidden repeats of side effects after a crash.
- An operator has to resume interrupted runs. Automatic resume for read-only steps is a possible future improvement.
