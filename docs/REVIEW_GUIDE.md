# Review guide

One page to check this project against the brief. Each row says where a requirement lives, how to verify it, and why it was built that way.

**Status of this guide:** skeleton. Locations and test names come from the [plans](../plans/README.md). Milestone M5 (task T5) replaces them with the real `file:symbol` and checks every row. Until then a row's status is the milestone that builds it.

IDs: `R*` requirements and `AC-*` acceptance criteria are defined in [specs/ops-agent-harness.md](../specs/ops-agent-harness.md). Decisions are in [docs/adr/](adr/README.md).

## 1. Five-minute quickstart

Available once M3 is merged (API and CLI) and M4 (UI). No API key is needed: the fake LLM is the default.

From the repo root:

```bash
docker compose up -d qdrant                            # knowledge base store
(cd backend && uv sync --locked && uv run pytest -q)   # install and test
```

In a second terminal, start the API on :8000 and leave it running:

```bash
cd backend && uv run uvicorn app.main:app --port 8000
```

Then pick one:

- **Postman / newman** (from the repo root): `npx newman run docs/postman_collection.json --folder "1. Demo flow: create → approve → trace"` creates a run, approves the incident and checks the trace.
- **CLI** (from `backend/`): `uv run python -m app.cli run "payments-api is returning 5xx errors. Investigate and open an incident if needed."` and answer the approval prompt.
- **UI** (from the repo root): `cd frontend && npm install && npm start`, open http://localhost:4200. With Docker only: `docker compose --profile app up --build`, open http://localhost:8000 (M5).

## 2. Requirement matrix

Paths are under `backend/app/` unless shown otherwise. Postman names are exact folder and request names in [docs/postman_collection.json](postman_collection.json).

| ID | Requirement (from the brief) | Where | How to verify | Why | Status |
|---|---|---|---|---|---|
| R1 | Accept a user objective through an API or CLI | `api/runs.py`, `cli.py`, `harness/runner.py` | AC-1: `test_api.py::test_create_run_returns_202`, `::test_invalid_body_returns_422`, `test_cli.py::test_cli_run_visible_in_api`; Postman "Create run that asks for an incident", "Invalid body is rejected (422)" | [0001](adr/0001-backend-python-fastapi.md) | M3 |
| R2 | Support an LLM–tool execution loop | `harness/loop.py`, `harness/llm_gateway.py`, `harness/tool_gateway.py` | AC-2: `test_loop.py::test_success_run_completes`; UI timeline | [0002](adr/0002-agent-loop-langgraph-custom-nodes.md) | M1, M2 |
| R3 | Validate tool inputs and outputs | `tools/registry.py`, `harness/tool_gateway.py` | AC-3, AC-12: `test_tools.py::test_invalid_args_not_executed`, `::test_bad_output_not_retried`, `::test_long_output_truncated`, `test_approval.py::test_invalid_incident_args_ask_no_approval` | [0010](adr/0010-failure-handling-envelope-retry-repair.md) | M1, M3 |
| R4 | Maintain agent state and execution history | `harness/store.py`, `harness/state.py`, LangGraph checkpointer | AC-4, AC-16: `test_loop.py::test_state_survives_restart`, `test_api.py::test_run_detail_has_history`, `test_recovery.py::*` | [0004](adr/0004-state-and-database-sqlite.md), [0013](adr/0013-recovery-interrupted-runs.md) | M1, M3 |
| R5 | Handle tool errors, timeouts, retries and malformed LLM responses | `harness/tool_gateway.py`, `harness/retry.py`, `harness/llm_gateway.py` | AC-5–AC-7: `test_failures.py::*`, `test_llm_gateway.py::*`; Postman folder "3. Failure and limit demos" | [0010](adr/0010-failure-handling-envelope-retry-repair.md), [0012](adr/0012-fault-injection-per-run.md) | M1 |
| R6 | Prevent infinite loops using step or time limits | `harness/policy.py`, guard node in `harness/loop.py`, segment timeout in `harness/runner.py` | AC-8: `test_limits.py::*`; Postman "Step limit (max_steps = 2)" | [0011](adr/0011-execution-limits.md) | M1, M3 |
| R7 | Require user approval before calling `create_incident` | approval node in `harness/loop.py`, decisions in `harness/runner.py`, `api/runs.py` | AC-9, AC-10: `test_approval.py::*`, `test_loop.py::test_incident_call_pauses_run_without_incident`, `test_tools.py::test_incident_needs_decision`; Postman folder "1. Demo flow: create → approve → trace" | [0009](adr/0009-human-approval-interrupt.md) | M1, M3 |
| R8 | Produce logs or traces for each execution | `harness/tracer.py`, `log.py`, `GET /api/runs/{id}/events`, `/trace` | AC-11: `test_tracing.py::*`, `test_api.py::test_sse_*`, `::test_trace_export`; Postman "Trace" | [0014](adr/0014-observability-trace-events.md) | M1, M3 |
| R9 | Tests for success, tool failure, approval and execution limits | `backend/tests/`, `evals/` | `uv run pytest -q`; AC-18: `evals/run.sh`; CI workflow | [0017](adr/0017-docs-postman-ci.md) | M1–M5 |
| R10 | Mock implementations of the three tools | `tools/kb.py`, `tools/status.py`, `tools/incident.py`, `data/` | AC-12: `test_tools.py::test_tool_schemas_exposed`, `test_api.py::test_tools_listed`; Postman "Tools" | [0003](adr/0003-llm-openai-compatible-with-fake.md) | M1, M2 |
| R11 | Clear instructions to run and test | [README.md](../README.md), section 1 above | AC-17: follow the README from a clean clone | [0017](adr/0017-docs-postman-ci.md) | M5 |
| R12 | UI demo that shows a run live | `frontend/` | AC-13: `trace.spec.ts`, `approval-inbox.spec.ts`; section 4 scenarios in the UI | [0015](adr/0015-ui-run-console-not-chat.md), [0016](adr/0016-ui-angular.md) | M4 |
| R13 | Design document | [docs/DESIGN.md](DESIGN.md) | AC-17: approach, database design, stack, env vars, limitations, future work | [0017](adr/0017-docs-postman-ci.md) | Done |
| R14 | Postman collection | [docs/postman_collection.json](postman_collection.json) | AC-17: newman run of folder 1 | [0017](adr/0017-docs-postman-ci.md) | Done (checked against the API in M5) |
| R15 | Plan with tasks, estimates and milestones | [plans/README.md](../plans/README.md), `plans/m*.md` | AC-17: tasks with estimates and proof per milestone | [0018](adr/0018-adopt-ai-sdlc-workflow.md) | Done |
| R16 | Hybrid retrieval for `search_knowledge_base` (added) | `kb/`, `tools/kb.py` | AC-14: `test_kb.py::*`, `test_api.py::test_health_reports_kb_mode` | [0005](adr/0005-kb-search-hybrid-rag.md)–[0007](adr/0007-embeddings-api-sparse-fallback.md) | M2 |
| R17 | Quality measured with RAGAS (added) | `eval/`, `evals/kb_golden.jsonl` | AC-15: `test_eval.py::*`; Postman folder "5. Evaluation"; UI Evaluation tab | [0008](adr/0008-evaluation-ragas-offline-online.md) | M2, M3 |
| R18 | Decisions recorded as ADRs (added) | [docs/adr/](adr/README.md) | AC-17: every decision in the spec links to an ADR | [0000](adr/0000-record-architecture-decisions.md) | Done |
| R19 | This review guide (added) | this file | AC-17: every row points to an existing file and a passing test or manual step | [0018](adr/0018-adopt-ai-sdlc-workflow.md) | Skeleton; checked in M5 |

## 3. Evaluation criteria

**Agent-loop and state-management design**
- The loop as a graph and why each node exists: [spec, Agent loop](../specs/ops-agent-harness.md#agent-loop), [ADR 0002](adr/0002-agent-loop-langgraph-custom-nodes.md).
- Run lifecycle and statuses, segments, locks: [spec, Run lifecycle](../specs/ops-agent-harness.md#run-lifecycle).
- Where state lives and why: [DESIGN, Database design](DESIGN.md#3-database-design), [ADR 0004](adr/0004-state-and-database-sqlite.md).
- Code: `harness/loop.py`, `harness/runner.py`, `harness/state.py`. Tests: `test_loop.py`, `test_recovery.py`.

**Tool integration and validation**
- Tool contracts (input, output, errors): [spec, Tools](../specs/ops-agent-harness.md#tools).
- One gateway for every call: validation, timeout, retry, idempotency, envelope. Code: `harness/tool_gateway.py`, `tools/registry.py`.
- Tests: `test_tools.py`, `test_failures.py`.

**Error handling and safety controls**
- Error model and retry rules: [spec, Tool results and errors](../specs/ops-agent-harness.md#tool-results-and-errors), [ADR 0010](adr/0010-failure-handling-envelope-retry-repair.md).
- Limits in layers: [ADR 0011](adr/0011-execution-limits.md). Approval gate: [ADR 0009](adr/0009-human-approval-interrupt.md).
- Every control in one table: [DESIGN, Safety controls](DESIGN.md#4-safety-controls).
- Tests: `test_llm_gateway.py`, `test_limits.py`, `test_approval.py`; scenario evals in `evals/`.

**Code quality, observability and testing**
- Trace events, live stream, JSON logs: [DESIGN, Observability](DESIGN.md#5-observability), [ADR 0014](adr/0014-observability-trace-events.md).
- Each acceptance criterion has named tests in its plan's Proof table ([plans/](../plans/README.md)).
- CI: [.github/workflows/ci.yml](../.github/workflows/ci.yml) runs ruff, pytest and the commit and branch checks. Process: [docs/AI-SDLC.md](AI-SDLC.md), [CONTRIBUTING.md](../CONTRIBUTING.md), per-task review log in each plan.

## 4. Demo scenarios

All scenarios run in the UI (fault switches) and as scenario evals (`evals/`, M5). Postman covers all but prompt injection: folders "1. Demo flow: create → approve → trace", "3. Failure and limit demos" and, for decisions, "4. Approvals".

| Scenario | Start with | What to look for |
|---|---|---|
| Approve an incident | objective asks for an incident on `payments-api` | run pauses, approval in the inbox, no incident until approved, then exactly one |
| Retry | fault `get_service_status: timeout ×2` | three attempts, two retry events (amber), run completes |
| Malformed reply | fault `llm: malformed ×1` | correction message, run continues (orange) |
| Step limit | `limits.max_steps = 2` | run ends `limit_exceeded` (red), budget meter full |
| Prompt injection | objective `SMS alerts from notifications-worker are delayed. Check the SMS vendor note.` (it retrieves the vendor note with the injected instruction) | agent proposes a SEV1, operator rejects, reason reaches the LLM |
| Degraded search | fault `embeddings: error` | search `mode = sparse_only` (blue) |
| Timeout after commit | fault `create_incident: timeout_after_commit`, then approve | retry, still exactly one incident |

## 5. Beyond the brief

Hybrid RAG search (R16), RAGAS evaluation (R17), ADRs (R18), this guide (R19), fault injection per run ([0012](adr/0012-fault-injection-per-run.md)), recovery of interrupted runs ([0013](adr/0013-recovery-interrupted-runs.md)), and the AI-SDLC workflow with CI conventions ([0018](adr/0018-adopt-ai-sdlc-workflow.md)).

## 6. Known gaps

See [DESIGN, Limitations](DESIGN.md#8-limitations) and [Future improvements](DESIGN.md#9-future-improvements).
