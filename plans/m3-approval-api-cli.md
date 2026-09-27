# Plan: M3 approval, API and CLI   (from [specs/ops-agent-harness.md](../specs/ops-agent-harness.md))

Status: draft (gate 1 before the milestone starts). Branch: `feat/m3-approval-api-cli`. PR title: `feat: M3 approval, API and CLI`.

Human decisions on approvals, the REST API with live events, recovery of interrupted runs, and the CLI. After this milestone the harness is usable from Postman and the terminal. The approval node itself (`interrupt()`) exists since M1.

## Files that change
- `backend/app/harness/runner.py` (edit) - approval rows, decide (conditional update, 409), resume with `Command`, expiry sweep, cancel closes approvals, resume of interrupted runs, startup recovery, per-run lock
- `backend/app/harness/loop.py` (edit) - apply decisions in the tools step (approve, reject envelope, edited args re-validated)
- `backend/app/auth.py` (new) - `current_user()` returns `anonymous`; approver token check
- `backend/app/api/runs.py` (new) - runs, events (SSE), trace, approvals, resume, cancel, audit events
- `backend/app/api/eval.py` (new) - start golden-set evaluation (SSE), latest report
- `backend/app/api/meta.py` (edit) - health with every dependency and knowledge-base mode, tools, incidents
- `backend/app/main.py` (edit) - lifespan: recovery scan, sweep task, routers
- `backend/app/cli.py` (new) - run, list, show, resume, ingest, eval; interactive approval
- `backend/tests/test_approval.py`, `test_api.py`, `test_recovery.py`, `test_cli.py` (new), `test_limits.py` (edit)

## Order of work
1. T1 → T2 → T3 → T4.

## Tasks
| ID | Task | Files | Skills | Depends on | Parallel | Est. |
|----|------|-------|--------|------------|----------|------|
| T1 | Decisions: approval rows, approve/reject/edit, 409 on a second decision, expiry sweep, cancel closes approvals (AC-3, AC-8, AC-9, AC-10) | harness/runner.py, harness/loop.py | ai-engineer | M2 | no | 1.5h |
| T2 | REST API: runs, SSE with replay, `Last-Event-ID` and close after `done`, trace, approvals, cancel, tools, incidents, health, eval endpoints, audit events, approver token (AC-1, AC-4, AC-9, AC-11, AC-12, AC-14, AC-15) | api/runs.py, api/eval.py, api/meta.py, auth.py, main.py | secure-api-review, ai-engineer | T1 | no | 2h |
| T3 | Recovery: startup scan to `interrupted` (API only), resume from the last checkpoint, `POST /api/runs/{id}/resume` (AC-16) | harness/runner.py, main.py, api/runs.py | secure-api-review, ai-engineer | T2 | no | 0.5h |
| T4 | CLI: run with live events and interactive approval (edited args validated like the API), list, show, resume, ingest, eval; `--faults` obeys `ALLOW_FAULT_INJECTION` (AC-1, AC-16) | cli.py | ai-engineer | T3 | no | 1h |

## Risks
- Streaming responses in tests: use `httpx2.AsyncClient` with `httpx2.ASGITransport(app)` (already a dev dependency) and read events until `done`.
- Timing in tests (TTL, sweep, segment timeout): make TTL and intervals configurable per test instead of sleeping for real minutes.
- Two decisions at once: the per-run lock and the conditional update must both hold; test with concurrent requests.
- The approval node re-runs on resume: keep it free of side effects and assert that no event or row is duplicated.

## Proof
| AC | Tests |
|----|-------|
| AC-1 | `test_api.py::test_create_run_returns_202`, `::test_invalid_body_returns_422`, `::test_faults_refused_when_disabled`; `test_cli.py::test_cli_run_visible_in_api` |
| AC-3 | `test_approval.py::test_invalid_incident_args_ask_no_approval` |
| AC-4 | `test_api.py::test_run_detail_has_history` (includes `usage`) |
| AC-8 | `test_limits.py::test_approval_wait_not_counted` |
| AC-9 | `test_approval.py::test_incident_not_created_before_approval`, `::test_approve_creates_one_incident`, `::test_reject_sends_reason_to_llm`, `::test_edit_uses_new_args`, `::test_invalid_edit_returns_422`, `::test_second_decision_returns_409`, `::test_expired_approval_rejects`, `::test_cancel_closes_pending_approval`, `::test_approver_token_required`; `test_api.py::test_list_pending_approvals`, `::test_cancel_final_run_returns_409` |
| AC-10 | `test_approval.py::test_incident_cap_blocks_without_approval` |
| AC-11 | `test_api.py::test_sse_replays_then_streams`, `::test_sse_resumes_after_last_event_id`, `::test_sse_closes_after_done`, `::test_trace_export`, `::test_audit_event_for_state_changes` |
| AC-12 | `test_api.py::test_tools_listed` |
| AC-14 | `test_api.py::test_health_reports_kb_mode` |
| AC-15 | `test_api.py::test_eval_endpoint_streams_report`, `::test_eval_without_kb_returns_503`, `::test_latest_report_404_when_none` |
| AC-16 | `test_recovery.py::test_running_becomes_interrupted_on_startup`, `::test_resume_finishes_run`, `::test_awaiting_approval_untouched`, `::test_cli_does_not_recover`; `test_api.py::test_resume_non_interrupted_returns_409` |

## Pipeline log
| Phase | Result | Notes |
|-------|--------|-------|
