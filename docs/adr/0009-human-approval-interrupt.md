# 0009. Human approval before `create_incident`

Status: Accepted · Date: 2026-09-26

## Context

`create_incident` writes to an external system. The brief requires user approval before it is called. Approvers may answer minutes later, and the server may restart in between.

## Options

| Option | Pros | Cons |
|---|---|---|
| Approve / reject | Simple. | To change the severity the operator must reject and hope the LLM proposes better. |
| Approve / reject / edit arguments | Operator fixes severity or title in one step. | Edited arguments must be validated again. |
| Policy: auto-approve SEV3–SEV4, human for SEV1–SEV2 | Realistic. | Breaks the brief ("always require approval"). |

## Decision

Approve, reject or edit, built on LangGraph `interrupt()`:

1. The tool registry marks `create_incident` as `requires_approval`.
2. The agent routes such calls to the `approval` node, which calls `interrupt({tool_call_id, tool, args})`. The run becomes `awaiting_approval`; its state is in the checkpoint.
3. The runner (outside the node) writes an `approvals` row: `pending`, `expires_at = now + approval.ttl_s`.
4. The decision comes from the API, CLI or UI:
   - `approve`: the call runs as proposed.
   - `reject` with a reason: the call does not run; the reason goes back to the LLM as the tool result.
   - `edit`: new arguments are validated with the same input schema (HTTP 422 if invalid), then the call runs.
5. The decision is saved with `UPDATE ... WHERE status = 'pending'`. A second decision gets HTTP 409.
6. Expired approvals are swept every `approval.sweep_s` and resumed as rejected ("approval expired").

Extra safety around the side effect:

- The tool gateway adds `idempotency_key = run_id:tool_call_id`. The incident system returns the existing incident for a known key, so a retry after a timeout never creates a duplicate.
- `max_incidents_per_run` caps incidents per run.
- If `APPROVER_TOKEN` is set, decisions need the `X-Approver-Token` header.

## Consequences

- No side effect happens before a human decides, and pending approvals survive restarts.
- The approval node must stay free of side effects before `interrupt()`, because LangGraph re-runs it on resume.
- Policy-based auto-approval is listed as a future improvement.
