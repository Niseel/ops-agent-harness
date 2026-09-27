# Plan: M1 harness core   (from [specs/ops-agent-harness.md](../specs/ops-agent-harness.md))

Status: draft (gate 1 before the milestone starts). Branch: `feat/m1-harness-core`. PR title: `feat: M1 harness core`.

The harness package end to end, without HTTP: a run can start from Python, loop through the LLM and tools, fail safely, stop at limits, and pause at the approval node. The knowledge-base tool arrives in M2; tests here register a test double under its name. Approval decisions, the API and the CLI arrive in M3.

## Files that change
- `backend/pyproject.toml` (edit) - add `langgraph`, `langgraph-checkpoint-sqlite`, `aiosqlite`, `openai`
- `backend/app/harness/state.py` (new) - `AgentState`, `RunStatus`, envelope and event models
- `backend/app/harness/store.py` (new) - SQLite schema and queries for `runs`, `approvals`, `events`, `evals`, `eval_reports`, `incidents`
- `backend/app/harness/tracer.py` (new) - emit events: insert, publish to subscribers, JSON log line
- `backend/app/harness/retry.py` (new) - exponential backoff with full jitter, shared by LLM and tool calls
- `backend/app/tools/faults.py` (new) - fault options model (tool, LLM, `embeddings`) and "does attempt n fail?" check, shared by both gateways
- `backend/app/llm/openai_compat.py`, `backend/app/llm/fake.py` (new) - OpenAI-compatible client, `FakePlanner`, `ScriptedLLM`
- `backend/app/harness/llm_gateway.py` (new) - call, retry, classify, repair, unique tool call ids, LLM faults
- `backend/app/tools/registry.py`, `status.py`, `incident.py` (new) - tool contracts and mocks
- `backend/app/harness/tool_gateway.py` (new) - validation, timeout, retry, idempotency, envelope, truncation, tool faults
- `backend/app/harness/policy.py`, `loop.py`, `runner.py` (new) - limits and clamping, LangGraph graph, start and run a segment
- `backend/prompts/system.md` (new) - system prompt; tool output is untrusted data
- `data/services.json` (new) - service fixtures
- `backend/tests/conftest.py` and the test files below (new)

## Order of work
1. T1.
2. T2 and T4 in parallel: they touch disjoint files and both only need T1.
3. T3 after T2.
4. T5 after all of them.

## Tasks
| ID | Task | Files | Skills | Depends on | Parallel | Est. |
|----|------|-------|--------|------------|----------|------|
| T1 | State models, SQLite store (all tables), tracer with live subscribers and JSON log, backoff helper, fault options model (AC-4, AC-11) | harness/state.py, store.py, tracer.py, retry.py, tools/faults.py | ai-engineer | - | no | 1.5h |
| T2 | LLM clients: OpenAI-compatible, `FakePlanner` (spec rules), `ScriptedLLM` | llm/* | ai-engineer | T1 | yes | 1h |
| T3 | LLM gateway: retries, reply classification, repair up to `max_repairs`, unique tool call ids, LLM faults, `llm` event with model, prompt hash, tokens and latency (AC-7, AC-11) | harness/llm_gateway.py | ai-engineer | T2 | no | 1h |
| T4 | Tool registry, `get_service_status`, `create_incident` (idempotency key), tool gateway with validation, timeout, retry, envelope, truncation and tool faults; the gateway refuses a `requires_approval` tool without an approve or edit decision (`blocked`); fixtures (AC-3, AC-5, AC-6, AC-10, AC-12) | tools/registry.py, status.py, incident.py, harness/tool_gateway.py, data/services.json | ai-engineer | T1 | yes | 2h |
| T5 | LangGraph loop: guard, agent with pure checks, approval node (`interrupt()` only), tools, finalize; limits and clamping; runner start, segment timeout, pause as `awaiting_approval`; system prompt (AC-2, AC-4, AC-8, AC-9 pause) | harness/loop.py, policy.py, runner.py, prompts/system.md | ai-engineer | T1–T4 | no | 2h |

`create_incident` can never run in M1. The tool gateway refuses it without a decision (defence in depth), and in the loop every call to a tool with `requires_approval` goes to the approval node, where the run stops as `awaiting_approval`. M3 adds decisions, so the REVIEW.md invariant holds from the first commit that adds the tool.

## Risks
- LangGraph 1.2 details: runtime context (`context_schema`), `AsyncSqliteSaver` setup, the shape of `__interrupt__` in the stream, and how a node reads non-serialisable objects. Check against the installed version before writing the loop.
- Checkpointer and own tables share one SQLite file. Use WAL mode and one connection per writer to avoid `database is locked`.
- `asyncio.timeout` can cancel inside a node. The last checkpoint must stay usable and the run status must still be written.
- Idempotency of `create_incident` relies on unique tool call ids per run (spec: LLM).

## Proof
| AC | Tests |
|----|-------|
| AC-2 | `test_loop.py::test_success_run_completes` (search test double + status + answer) |
| AC-3 | `test_tools.py::test_invalid_args_not_executed`, `::test_bad_output_not_retried`, `::test_long_output_truncated` |
| AC-9 (guard) | `test_tools.py::test_incident_needs_decision` |
| AC-4 | `test_loop.py::test_state_survives_restart` |
| AC-5 | `test_failures.py::test_timeout_twice_then_success` |
| AC-6 | `test_failures.py::test_all_attempts_fail_run_continues` |
| AC-7 | `test_llm_gateway.py::test_malformed_reply_repaired`, `::test_two_repairs_then_valid_completes`, `::test_third_malformed_reply_fails_run`, `::test_llm_unavailable_fails_run`, `::test_tool_call_ids_made_unique` |
| AC-8 | `test_limits.py::test_max_steps_stops_run`, `::test_max_tool_calls_blocks_extra_call`, `::test_exact_max_tool_calls_then_answer_completes`, `::test_repeat_call_blocked`, `::test_segment_timeout_ends_timed_out`, `::test_limits_clamped_to_config` |
| AC-9 (pause, no incident) | `test_loop.py::test_incident_call_pauses_run_without_incident`; the `pending` approval row is checked in M3 |
| AC-10 | `test_failures.py::test_timeout_after_commit_creates_one_incident` |
| AC-11 | `test_tracing.py::test_events_have_increasing_seq`, `::test_events_cover_every_step`, `::test_one_done_event_per_run`, `::test_log_line_is_json_with_run_id`, `::test_secrets_masked`; `test_llm_gateway.py::test_llm_event_has_usage_and_prompt_sha` |
| AC-12 | `test_tools.py::test_tool_schemas_exposed`, `::test_unknown_service_not_found` |

## Pipeline log
| Phase | Result | Notes |
|-------|--------|-------|
