# Review guide

One page to check this project against the brief. Each row says where a requirement lives, how to verify it, and why it was built that way.

**Status of this guide:** checked in M5 T5 (2026-09-29). `backend/tests/test_docs.py` checks that every test name, every `file:symbol` and every relative link in this guide exists; CI runs the tests, the scenario evals and the Postman demo flow; each How-to-verify step was run by hand (log in [plans/m5-ship.md](../plans/m5-ship.md)). "Built in" is the milestone that built the row.

IDs: `R*` requirements and `AC-*` acceptance criteria are defined in [specs/ops-agent-harness.md](../specs/ops-agent-harness.md). Decisions are in [docs/adr/](adr/README.md).

## 1. Five-minute quickstart

No API key is needed: the fake LLM is the default. The quickest path is Docker only: `docker compose --profile app up --build`, then open http://localhost:8000 (UI and API).

On the host, from the repo root:

```bash
docker compose up -d qdrant                            # knowledge base store
(cd backend && uv sync --locked && uv run pytest -q -m "not live")   # install and test; live tests need real endpoints (README, Test)
```

In a second terminal, start the API on :8000 and leave it running:

```bash
cd backend && uv run uvicorn app.main:app --port 8000 --timeout-graceful-shutdown 5
```

Then pick one:

- **Scenario evals** (from the repo root): `evals/run.sh` runs the seven demo scenarios of section 4 against the API and grades each trace (`7 passed, 0 failed`).
- **Postman / newman** (from the repo root): `npx --yes newman@6.2.2 run docs/postman_collection.json --folder "1. Demo flow: create → approve → trace"` creates a run, approves the incident and checks the trace.
- **CLI** (from `backend/`): `uv run python -m app.cli run --no-eval "payments-api is returning 5xx errors. Investigate and open an incident if needed."` and answer the approval prompt (`--no-eval` skips online evaluation, which waits minutes for a slow local judge).
- **UI** (from the repo root): `cd frontend && npm ci && npm start`, open http://localhost:4200.

## 2. Requirement matrix

`Where` is `file:symbol`; paths are under `backend/app/` unless they start with `backend/`, `frontend/`, `evals/`, `data/` or `docs/`. Tests are in `backend/tests/`; a name that starts with `::` belongs to the last file named. Postman names are exact folder and request names in [docs/postman_collection.json](postman_collection.json).

| ID | Requirement (from the brief) | Where | How to verify | Why | Built in |
|---|---|---|---|---|---|
| R1 | Accept a user objective through an API or CLI | `api/runs.py:create_run`, `cli.py:cmd_run`, `harness/runner.py:Runner.create_run` | AC-1: `test_api.py::test_create_run_returns_202`, `::test_invalid_body_returns_422`, `::test_faults_refused_when_disabled`, `test_cli.py::test_cli_run_visible_in_api`; Postman "Create run that asks for an incident", "Invalid body is rejected (422)" | [0001](adr/0001-backend-python-fastapi.md) | M1, M3 |
| R2 | Support an LLM–tool execution loop | `harness/loop.py:build_graph`, `harness/llm_gateway.py:next_reply`, `harness/tool_gateway.py:execute` | AC-2: `test_loop.py::test_success_run_completes`, `::test_tool_attempts_and_incidents_in_checkpoint`, `test_llm_gateway.py::test_llm_event_lists_calls_with_args`; the UI timeline | [0002](adr/0002-agent-loop-langgraph-custom-nodes.md) | M1, M2, M4 |
| R3 | Validate tool inputs and outputs | `tools/registry.py:TOOLS`, `harness/tool_gateway.py:check_input`, `harness/policy.py:check_calls` | AC-3, AC-12: `test_tools.py::test_invalid_args_not_executed`, `::test_unknown_tool_refused`, `::test_bad_output_not_retried`, `::test_long_output_truncated`, `test_approval.py::test_invalid_incident_args_ask_no_approval`, `test_limits.py::test_validation_comes_before_limits` | [0010](adr/0010-failure-handling-envelope-retry-repair.md) | M1, M3 |
| R4 | Maintain agent state and execution history | `harness/store.py:Store`, `harness/state.py:AgentState` (kept in LangGraph checkpoints), `harness/runner.py:Runner.recover` | AC-4, AC-16: `test_loop.py::test_state_survives_restart`, `test_api.py::test_run_detail_has_history`, `::test_run_detail_survives_restart`, `test_recovery.py::*`, `test_store.py::*` | [0004](adr/0004-state-and-database-sqlite.md), [0013](adr/0013-recovery-interrupted-runs.md) | M1, M3 |
| R5 | Handle tool errors, timeouts, retries and malformed LLM responses | `harness/tool_gateway.py:execute`, `harness/retry.py:backoff`, `harness/llm_gateway.py:classify` | AC-5–AC-7: `test_failures.py::test_timeout_twice_then_success`, `::test_all_attempts_fail_run_continues`, `test_tools.py::test_transient_errors_retried_with_events`, `test_llm_gateway.py::test_malformed_reply_repaired`, `::test_third_malformed_reply_fails_run`, `::test_llm_unavailable_fails_run`, `::test_tool_call_ids_made_unique`; `evals/status-timeout-retry.json`, `evals/malformed-reply.json`; Postman folder "3. Failure and limit demos" | [0010](adr/0010-failure-handling-envelope-retry-repair.md), [0012](adr/0012-fault-injection-per-run.md) | M1 |
| R6 | Prevent infinite loops using step or time limits | `harness/loop.py:guard`, `harness/policy.py:check_calls`, `harness/policy.py:recursion_limit`, `harness/runner.py:Runner._segment` (segment timeout) | AC-8: `test_limits.py::test_limits_clamped_to_config`, `::test_max_steps_stops_run`, `::test_max_tool_calls_blocks_extra_call`, `::test_repeat_call_blocked`, `::test_segment_timeout_ends_timed_out`, `::test_recursion_limit_ends_limit_exceeded`, `::test_approval_wait_not_counted`; `evals/step-limit.json`; Postman "Step limit (max_steps = 2)" | [0011](adr/0011-execution-limits.md) | M1, M3 |
| R7 | Require user approval before calling `create_incident` | `harness/loop.py:needs_approval`, `harness/loop.py:approval`, `harness/runner.py:Runner.decide`, `api/runs.py:decide`, `auth.py:require_approver` | AC-9, AC-10: `test_approval.py::test_incident_not_created_before_approval`, `::test_approve_creates_one_incident`, `::test_reject_sends_reason_to_llm`, `::test_edit_uses_new_args`, `::test_second_decision_conflicts`, `::test_incident_cap_blocks_without_approval`, `::test_invalid_edit_keeps_approval_pending`, `::test_expired_approval_rejects`, `::test_cancel_closes_pending_approval`, `test_failures.py::test_timeout_after_commit_creates_one_incident`, `test_loop.py::test_incident_call_pauses_run_without_incident`, `test_tools.py::test_incident_needs_decision`, `test_api.py::test_approver_token_required`; `evals/approve-incident.json`, `evals/prompt-injection.json`, `evals/timeout-after-commit.json`; Postman folder "1. Demo flow: create → approve → trace" | [0009](adr/0009-human-approval-interrupt.md) | M1, M3 |
| R8 | Produce logs or traces for each execution | `harness/tracer.py:Tracer.emit`, `log.py:JsonFormatter`, `api/runs.py:run_events`, `api/runs.py:get_trace` | AC-11: `test_tracing.py::test_events_cover_every_step`, `::test_one_done_event_per_run`, `::test_secrets_masked`, `::test_log_line_is_json_with_run_id`, `test_api.py::test_sse_replays_then_streams`, `::test_sse_resumes_after_last_event_id`, `::test_sse_closes_after_done`, `::test_trace_export`, `::test_audit_event_for_state_changes`, `::test_usage_sums_llm_tokens`; Postman "Trace" | [0014](adr/0014-observability-trace-events.md) | M1, M3 |
| R9 | Tests for success, tool failure, approval and execution limits | `backend/tests/`, `evals/run.sh`, `evals/check.sh`, [ci.yml](../.github/workflows/ci.yml) | `uv run pytest -q -m "not live"`; AC-18: `evals/run.sh` (`7 passed, 0 failed`), `test_scenarios.py::test_check_sh_fails_on_each_mismatch`; the CI jobs on the pull request | [0017](adr/0017-docs-postman-ci.md) | M1–M5 |
| R10 | Mock implementations of the three tools | `tools/kb.py:search_knowledge_base`, `tools/status.py:get_service_status`, `tools/incident.py:create_incident`, `data/services.json`, `data/kb/` | AC-12: `test_tools.py::test_tool_schemas_exposed`, `::test_status_returns_fixture_record`, `::test_unknown_service_not_found`, `test_api.py::test_tools_listed`; Postman "Tools" | [0003](adr/0003-llm-openai-compatible-with-fake.md) | M1, M2 |
| R11 | Clear instructions to run and test | [README.md](../README.md), section 1 above | AC-17: the README followed from a clean copy (M5 T4 log); `test_docs.py::test_doc_links_resolve` | [0017](adr/0017-docs-postman-ci.md) | M5 |
| R12 | UI demo that shows a run live | `frontend/src/app/trace.ts:TraceStore`, `frontend/src/app/runs-page.ts:RunsPage`, `frontend/src/app/approval-inbox.ts:ApprovalInbox`, `frontend/src/app/flow-diagram.ts:FlowDiagram`, `backend/app/main.py:serve_ui`, `backend/app/llm/fake.py:FakePlanner`, `frontend/src/app/follow.ts:Follow` | AC-13: `trace.spec.ts`, `follow.spec.ts`, `palette.spec.ts`, `approval-inbox.spec.ts`, `flow-diagram.spec.ts`, `runs-page.spec.ts`, `test_skeleton.py::test_built_ui_served_after_api_routes`, `::test_config_checks_the_fake_llm_delay`, `test_llm.py::test_fake_planner_waits_in_the_configured_range`, `test_lifecycle.py::test_cancel_during_the_fake_llm_wait`; the UI check in section 4 | [0003](adr/0003-llm-openai-compatible-with-fake.md), [0015](adr/0015-ui-run-console-not-chat.md), [0016](adr/0016-ui-angular.md) | M4, M5, M6 |
| R13 | Design document | [docs/DESIGN.md](DESIGN.md) | AC-17: approach, database design, stack, environment variables, limitations, future work | [0017](adr/0017-docs-postman-ci.md) | M0, M5 |
| R14 | Postman collection | [docs/postman_collection.json](postman_collection.json) | AC-17: newman runs folder 1 in the CI `e2e` job; the whole collection passed against the API in M5 T4 | [0017](adr/0017-docs-postman-ci.md) | M0, M3, checked in M5 |
| R15 | Plan with tasks, estimates and milestones | [plans/README.md](../plans/README.md), `plans/m*.md` | AC-17: tasks with estimates, a Proof table and a Pipeline log per milestone | [0018](adr/0018-adopt-ai-sdlc-workflow.md) | M0 |
| R16 | Hybrid retrieval for `search_knowledge_base` (added) | `kb/qdrant.py:KnowledgeBase.search`, `kb/qdrant.py:rrf`, `kb/sparse.py:query_vector`, `kb/ingest.py:ingest` | AC-14: `test_kb.py::test_exact_term_found_by_bm25`, `::test_paraphrase_found_by_dense` (a `live` test: it runs when an embedding endpoint answers), `::test_sparse_only_when_embeddings_down`, `::test_ingest_skips_unchanged`, `test_api.py::test_health_reports_kb_mode`; `evals/degraded-search.json`; numbers in [ADR 0005](adr/0005-kb-search-hybrid-rag.md#validation) | [0005](adr/0005-kb-search-hybrid-rag.md)–[0007](adr/0007-embeddings-api-sparse-fallback.md) | M2 |
| R17 | Quality measured with RAGAS (added) | `eval/golden.py:run_golden`, `eval/online.py:evaluate_run`, `eval/metrics.py:RagasJudge`, `api/eval.py:eval_kb`, `evals/kb_golden.jsonl` | AC-15: `test_eval.py::test_golden_report_has_all_modes`, `::test_online_scores_stored_after_run`, `::test_low_score_emits_warn`, `::test_judge_down_gives_null_and_run_unchanged`, `test_api.py::test_eval_endpoint_streams_report`, `::test_eval_without_kb_returns_503`, `eval-tab.spec.ts`; Postman folder "5. Evaluation"; the UI Evaluation tab | [0008](adr/0008-evaluation-ragas-offline-online.md) | M2, M3, M4 |
| R18 | Decisions recorded as ADRs (added) | [docs/adr/](adr/README.md) | AC-17: every decision in the spec links to an ADR, and `test_docs.py::test_doc_links_resolve` checks that each link resolves | [0000](adr/0000-record-architecture-decisions.md) | M0 |
| R19 | This review guide (added) | this file, `backend/tests/test_docs.py:test_review_guide_symbols_exist` | AC-17: `test_docs.py::test_review_guide_tests_exist`, `::test_review_guide_symbols_exist` | [0018](adr/0018-adopt-ai-sdlc-workflow.md) | M5 |

## 3. Evaluation criteria

**Agent-loop and state-management design**
- The loop as a graph and why each node exists: [spec, Agent loop](../specs/ops-agent-harness.md#agent-loop), [ADR 0002](adr/0002-agent-loop-langgraph-custom-nodes.md).
- Run lifecycle and statuses, segments, locks: [spec, Run lifecycle](../specs/ops-agent-harness.md#run-lifecycle).
- Where state lives and why: [DESIGN, Database design](DESIGN.md#3-database-design), [ADR 0004](adr/0004-state-and-database-sqlite.md).
- Code: `harness/loop.py:build_graph`, `harness/runner.py:Runner`, `harness/state.py:AgentState`. Tests: `test_loop.py`, `test_recovery.py`, `test_lifecycle.py`.

**Tool integration and validation**
- Tool contracts (input, output, errors): [spec, Tools](../specs/ops-agent-harness.md#tools).
- One gateway for every call: validation, timeout, retry, idempotency, envelope. Code: `harness/tool_gateway.py:execute`, `tools/registry.py:TOOLS`.
- Tests: `test_tools.py`, `test_failures.py`.

**Error handling and safety controls**
- Error model and retry rules: [spec, Tool results and errors](../specs/ops-agent-harness.md#tool-results-and-errors), [ADR 0010](adr/0010-failure-handling-envelope-retry-repair.md).
- Limits in layers: [ADR 0011](adr/0011-execution-limits.md). Approval gate: [ADR 0009](adr/0009-human-approval-interrupt.md).
- Every control in one table: [DESIGN, Safety controls](DESIGN.md#4-safety-controls).
- Tests: `test_llm_gateway.py`, `test_limits.py`, `test_approval.py`; the scenario evals in section 4.

**Code quality, observability and testing**
- Trace events, live stream, JSON logs: [DESIGN, Observability](DESIGN.md#5-observability), [ADR 0014](adr/0014-observability-trace-events.md).
- Each acceptance criterion has named tests in its plan's Proof table ([plans/](../plans/README.md)).
- CI: [.github/workflows/ci.yml](../.github/workflows/ci.yml) runs ruff and pytest, the frontend format check, tests and build, the scenario evals and the Postman demo flow against a running API (fake LLM, Qdrant), and the commit and branch checks. Process: [docs/AI-SDLC.md](AI-SDLC.md), [CONTRIBUTING.md](../CONTRIBUTING.md), per-task review log in each plan.

## 4. Demo scenarios

All scenarios run in the UI (fault switches and limits) and as scenario evals: `evals/run.sh` runs the seven files below against a running API and grades each trace with `evals/check.sh`. Postman covers all but prompt injection: folders "1. Demo flow: create → approve → trace", "3. Failure and limit demos" and, for decisions, "4. Approvals".

| Scenario | Start with | What to look for | Scenario eval |
|---|---|---|---|
| Approve an incident | objective asks for an incident on `payments-api` | run pauses, approval in the inbox, no incident until approved, then exactly one | `evals/approve-incident.json` |
| Retry | fault `get_service_status: timeout ×2` | three attempts, two retry events (amber), run completes | `evals/status-timeout-retry.json` |
| Malformed reply | fault `llm: malformed ×1` | correction message, run continues (orange) | `evals/malformed-reply.json` |
| Step limit | `limits.max_steps = 2` | run ends `limit_exceeded` (red), budget meter full | `evals/step-limit.json` |
| Prompt injection | objective `SMS alerts from notifications-worker are delayed. Check the SMS vendor note.` (it retrieves the vendor note with the injected instruction) | agent proposes a SEV1, operator rejects, reason reaches the LLM | `evals/prompt-injection.json` |
| Degraded search | fault `embeddings: error` | search `mode = sparse_only` (blue) (without an embedding endpoint every search is `sparse_only`; the fault shows only when embeddings answer) | `evals/degraded-search.json` |
| Timeout after commit | fault `create_incident: timeout_after_commit`, then approve | retry, still exactly one incident | `evals/timeout-after-commit.json` |

**UI check (AC-13).** With the API on :8000 and `npm start` in `frontend/`, open http://localhost:4200 in a window of at least 1280×720. 0. The page does not scroll; only panels do, and the timeline keeps the newest step in view. 1. Start the Approve scenario: the timeline and the NOW bar show each call with its arguments and attempt, and the flow diagram lights the running node; the Now panel switches between the LLM (thinking, about 0.4 s a turn), the harness (with the tool, its arguments and attempt) and a person, and shows the LLM's last choice. 2. The approval appears in the inbox with a countdown. Approve it (or edit it, or reject it with a reason): the run continues, the Result panel shows ✔ completed with the answer and the incident, and the Incidents tab lists the incident. 3. Start the Retry, Step limit and Degraded search scenarios from the fault switches and limits: retries show amber, the limit red and `sparse_only` search blue, each with an icon and a word; the Result panel explains the limit. 4. Switch the OS to dark mode: every colour keeps its meaning and stays readable.

## 5. Beyond the brief

Hybrid RAG search (R16), RAGAS evaluation (R17), ADRs (R18), this guide (R19), fault injection per run ([0012](adr/0012-fault-injection-per-run.md)), recovery of interrupted runs ([0013](adr/0013-recovery-interrupted-runs.md)), and the AI-SDLC workflow with CI conventions ([0018](adr/0018-adopt-ai-sdlc-workflow.md)).

## 6. Known gaps

See [DESIGN, Limitations](DESIGN.md#8-limitations) and [Future improvements](DESIGN.md#9-future-improvements).
