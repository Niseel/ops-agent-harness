# Plan: M3 approval, API and CLI   (from [specs/ops-agent-harness.md](../specs/ops-agent-harness.md))

Status: approved (gate 1, 2026-09-28). Branch: `feat/m3-approval-api-cli`. PR title: `feat: M3 approval, API and CLI`.

Human decisions on approvals, the run lifecycle around them (background segments, cancel, expiry, recovery), the REST API with live events, and the CLI. After this milestone the harness is usable from Postman and the terminal. The approval node (`interrupt()`) exists since M1; online evaluation runs in the runner after `done` since M2.

## Files that change
- `backend/app/harness/store.py` (edit, T1) - write lock and shielded transactions; run list; approval queries; conditional finish, pause, decide and cancel; expiry, recovery and resume queries; `ping`
- `backend/app/harness/runner.py` (edit; T2, T3, T4, T5) - approval rows at pause, `decide`, `continue_run`, `audit` (T2); per-run lock, `spawn`, `start`, `cancel`, `sweep`, `recover`, `request_resume`, `close` (T3); `run_detail` (T4); `llm_reachable` (T5)
- `backend/app/harness/loop.py` (edit, T2) - the tools node turns a reject decision into the `rejected` envelope
- `backend/app/harness/tool_gateway.py` (edit, T2) - attention `info` for a `rejected` call
- `backend/app/auth.py` (new, T4) - `current_user()`, approver token check
- `backend/app/log.py` (edit, T4) - `secret_forms()`, shared by the log formatter and the masked API responses
- `backend/app/api/__init__.py` (edit, T4) - `get_runner`, `MaskedJSONResponse`
- `backend/app/api/runs.py` (new T4, edit T5) - runs, trace, approvals, decisions, resume, cancel (T4); live events (T5)
- `backend/app/api/meta.py` (edit; T4, T5) - tools and incidents (T4); health (T5)
- `backend/app/api/eval.py` (new, T5) - golden-set evaluation over SSE, latest report
- `backend/app/main.py` (edit, T4) - lifespan (runner, recovery, ingest, sweep, shutdown), routers, error handlers, masked responses
- `backend/app/llm/openai_compat.py` (edit, T5) - `OpenAICompatClient.reachable()`
- `backend/app/kb/ingest.py` (edit, T6) - `force`
- `backend/app/cli.py` (new, T6) - run, list, show, resume, ingest, eval
- `backend/pyproject.toml`, `backend/uv.lock` (edit, T5) - `fastapi>=0.135.0` (native SSE); `uv add`, no new package
- `backend/tests/conftest.py` (edit; T3, T4, T5); `test_store.py` (edit, T1); `test_approval.py` (new T2, edit T3); `test_limits.py` (edit, T2); `test_lifecycle.py` (new, T3); `test_recovery.py` (new T3, edit T4); `test_api.py` (new T4, edit T5); `test_skeleton.py` (edit T5, changed on purpose); `test_cli.py` (new, T6); `test_kb.py` (edit, T6)
- `specs/ops-agent-harness.md` (edit; T2–T6), `docs/DESIGN.md` (edit; T3–T5), `docs/adr/0013-recovery-interrupted-runs.md`, `docs/adr/README.md` (edit, T3), `README.md`, `docs/REVIEW_GUIDE.md` (edit, T5) - exact notes in Doc changes

## Order of work
1. T1 → T2 → T3 → T4 → T5 → T6. One commit each; stop after each commit for review.
2. T5 and T6 need only T3 and T4, so T6 may go before T5. Nothing else can move.

## Tasks
| ID | Task | Files | Skills | Depends on | Parallel | Est. |
|----|------|-------|--------|------------|----------|------|
| T1 | Store: write lock and shielded transactions; `list_runs`; approval queries; conditional `finish_run`, `pause_run`, `decide_approval`, `cancel_run`; `expired_approvals`, `mark_interrupted`, `resume_run`, `ping` | harness/store.py, tests/test_store.py | ai-engineer | M2 | no | 0.5h |
| T2 | Decisions in the runner: approval row and `approval_id`, `expires_at`, `run_error` at pause; `decide` (404, 409, 422 order, events, audit); `continue_run` resumes with `Command(resume=...)` rebuilt from the approval row; approvals asked one at a time; reject gives the `rejected` envelope with attention `info` (AC-3, AC-8, AC-9, AC-10) | harness/runner.py, harness/loop.py, harness/tool_gateway.py, tests/test_approval.py, tests/test_limits.py, specs/ops-agent-harness.md | ai-engineer | T1 | no | 1h |
| T3 | Run lifecycle: per-run lock, `spawn` and `start`, `close` cancels segments; `cancel` (one transaction, stops a running segment, one `done`); expiry sweep (actor `system`); `recover` and `request_resume`; decide and expiry wait for a pausing segment (AC-9 expiry and cancel, AC-16 runner part) | harness/runner.py, tests/conftest.py, tests/test_lifecycle.py, tests/test_approval.py, tests/test_recovery.py, specs/ops-agent-harness.md, docs/DESIGN.md, docs/adr/0013, docs/adr/README.md | ai-engineer | T2 | no | 1h |
| T4 | REST API core: `auth.py` (`current_user`, constant-time token check); app wiring (lifespan with recovery and the sweep, error handlers, masked responses); runs (create, list, detail with `calls`, `approvals`, `evals`, `usage`; trace); approvals (list, decide); resume; cancel; tools; incidents; audit events (AC-1, AC-4, AC-9, AC-11, AC-12, AC-16) | auth.py, log.py, api/__init__.py, api/runs.py, api/meta.py, main.py, harness/runner.py, tests/conftest.py, tests/test_api.py, tests/test_recovery.py, specs/ops-agent-harness.md, docs/DESIGN.md | secure-api-review, ai-engineer | T3 | no | 1.25h |
| T5 | Streams, evaluation and health: run events over SSE (replay, live, `Last-Event-ID`, keep-alive, close after `done`); golden-set evaluation over SSE with the 503 check and the audit event; latest report; health with every dependency and the knowledge-base mode; run command with `--timeout-graceful-shutdown` (AC-11, AC-14, AC-15) | api/runs.py, api/eval.py, api/meta.py, harness/runner.py, llm/openai_compat.py, tests/conftest.py, tests/test_api.py, tests/test_skeleton.py, pyproject (`fastapi>=0.135.0`), specs/ops-agent-harness.md, docs/DESIGN.md, README.md, docs/REVIEW_GUIDE.md | secure-api-review, ai-engineer | T4 | yes (with T6) | 1h |
| T6 | CLI: `run` with live events and the interactive approval (edits validated like the API), `list`, `show`, `resume`, `ingest [--force]`, `eval`; exit codes; no recovery (AC-1, AC-16) | cli.py, kb/ingest.py, tests/test_cli.py, tests/test_kb.py, specs/ops-agent-harness.md | ai-engineer | T3, T4 | yes (with T5) | 0.75h |

The first draft had 4 tasks (5h). Its T1 mixed store transactions with runner decisions, and its T2 (2h) held every route, the event stream, evaluation and health. Now: store (T1), decisions (T2), lifecycle with cancel, sweep and recovery (T3), API core (T4), streams, evaluation and health (T5), CLI (T6). The estimate grows by 0.5h to 5.5h: the draft had no time for store transactions or the crash-safe resume path.

## Interfaces
Names that later tasks and milestones rely on. Exact signatures are the coder's choice.

| Module (task) | Exposes | Does |
|---|---|---|
| `harness/store.py` (T1) | `list_runs`, `finish_run`, `pause_run`, `get_approval`, `approval_for_call`, `list_approvals`, `decide_approval`, `expired_approvals`, `cancel_run`, `mark_interrupted`, `resume_run`, `ping` | "Store" below |
| `harness/runner.py` (T2) | `NotFound(LookupError)`, `Conflict(Exception)`, `resume_value(approval)`, `Runner.decide(run_id, approval_id, *, decision, reason=None, args=None, actor)`, `Runner.continue_run(run_id, *, llm_client=None)`, `Runner.audit(run_id, actor, action, entity_id)` | "Approval rows and decisions", "Which entry runs the graph" |
| `harness/runner.py` (T3) | `Runner.spawn(run_id, fn)`, `start(run_id)`, `cancel(run_id, *, actor)`, `sweep()`, `sweep_forever(interval_s)`, `recover()`, `request_resume(run_id, *, actor)`, `close()` | "Per-run lock", "Cancel", "Expiry sweep", "Recovery" |
| `harness/runner.py` (T4, T5) | `run_detail(run_id)` (T4), `llm_reachable()` (T5) | "API", "Health" |
| `auth.py` (T4) | `current_user()`, `require_approver` | Returns `"anonymous"`; token dependency |
| `log.py` (T4) | `secret_forms(secrets)` | Plain and JSON-escaped forms of each secret, longest first (moved out of `_Masked`) |
| `api/__init__.py` (T4) | `get_runner(request)`, `MaskedJSONResponse` | Runner from `app.state`; secrets masked in every JSON body |
| `main.py` (T4) | `lifespan`, `app` | "API" |
| `llm/openai_compat.py` (T5) | `OpenAICompatClient.reachable()` | `GET /models` within 2 s |
| `kb/ingest.py` (T6) | `ingest(kb, docs_dir, *, force=False)` | `force` rebuilds even when unchanged |
| `cli.py` (T6) | `amain(argv) -> int`, `main(argv=None) -> int` | "CLI" |

## Rules pinned by this plan
Taken from the spec, the ADRs, the M1 and M2 handoffs, the API standard, the Postman collection and the current code, so the coder does not have to decide. Decisions the owner made in round 1 (2026-09-28) are marked "(owner, round 1)".

**General**
- Timestamps only from `app.clock.now_iso()`. An approval's `created_at` and `expires_at` come from one `now = time.time()`: `now_iso(now)` and `now_iso(now + cfg.approval.ttl_s)`. Expiry compares the text (`expires_at <= now_iso()`); the format sorts correctly.
- Loggers: `app.runner` (lifecycle), `app.api` (unexpected errors), `app.eval` (evaluation job).
- Functions that tests patch are called through their module, as in M1 and M2: `qdrant.get_kb()`, `metrics.get_judge()`, `golden.run_golden()`.
- FastAPI parameters use `Annotated[..., Depends(...)]`, `Annotated[..., Query(...)]` and `Annotated[..., Header(...)]`: ruff `B008` refuses calls in argument defaults.
- Polling loops in tests are `for` loops that check, then sleep: ruff `ASYNC110` refuses a `while` loop whose body only sleeps.
- The caller always gives the `actor`: `current_user()` from the API and the CLI, `"system"` from the sweep. The runner never imports `auth.py`.

**Store: one connection, locked writes, shielded transactions** (T1)
- `Store` has one aiosqlite connection for every coroutine. Statements from different coroutines on one connection share its transaction, so a write from one coroutine can land inside another's open transaction and be rolled back with it. So `Store` gets `self._write = asyncio.Lock()`, and every write method takes it, the existing ones too (`create_run`, `update_run`, `insert_event`, `create_incident`, `insert_eval`, `insert_eval_report`). Reads do not lock.
- Multi-statement writes go through a private helper: under the lock, `BEGIN IMMEDIATE`, the statements, `COMMIT`; any exception → `ROLLBACK` and re-raise. The public method awaits the helper through `asyncio.shield(...)`, with `BEGIN` inside the shielded part: a cancelled caller gets `CancelledError`, but the transaction still ends with `COMMIT` or `ROLLBACK`. A cancelled `await` does not stop a statement already queued on the connection's thread, so without the shield a transaction could stay open and lock the file (the M1 T6 lesson).
- Inside a transaction use only the connection's `execute`. Never call another store method there (the lock is not re-entrant) and never emit an event; emit after the method returns.
- Approvals come back in API shape, parsed like `get_run` parses `options`: `{id, run_id, tool_call_id, tool, args, status, decision, reason, decided_by, created_at, decided_at, expires_at}` (`args` from `args_json`, `decision` from `decision_json`).

| Method | Does |
|---|---|
| `list_runs(limit)` | Summaries `{id, objective, status, llm_mode, steps, tool_calls, created_at, updated_at}`, newest first (`created_at DESC, rowid DESC`) |
| `finish_run(id, **fields) -> bool` | `UPDATE runs SET ..., updated_at WHERE id = ? AND status = 'running'`; true when a row changed. Same column whitelist as `update_run` |
| `pause_run(run_id, *, steps, tool_calls, calls, created_at, expires_at) -> list[dict] or None` | Transaction: run → `awaiting_approval` (with `steps`, `tool_calls`) only from `running`, else ROLLBACK and None. For each `{tool_call_id, tool, args}`: `INSERT ... ON CONFLICT (run_id, tool_call_id) DO NOTHING` (id `uuid4().hex`, status `pending`). Returns the rows of those calls |
| `get_approval(id)`, `approval_for_call(run_id, tool_call_id)` | One row or None |
| `list_approvals(*, run_id=None, status=None)` | Oldest first (`created_at, rowid`) |
| `decide_approval(id, *, status, decision, reason, decided_by) -> dict or None` | Transaction: `UPDATE approvals SET status, decision_json, reason, decided_by, decided_at WHERE id = ? AND status = 'pending' AND (SELECT status FROM runs WHERE id = approvals.run_id) = 'awaiting_approval'`; when a row changed, `UPDATE runs SET status = 'running' ... WHERE id = ? AND status = 'awaiting_approval'`. Returns the updated approval, or None when nothing changed |
| `expired_approvals(now)` | `pending` rows with `expires_at <= now` whose run is `awaiting_approval`, oldest `expires_at` first |
| `cancel_run(id, *, decided_by) -> int or None` | Transaction: run → `cancelled` with `finished_at`, only from `running`, `awaiting_approval` or `interrupted`, else None. Its `pending` approvals → `cancelled` with `decided_by`, `decided_at` and reason `run cancelled`. Returns how many approvals it closed |
| `mark_interrupted() -> list[str]` | Under the lock: the ids of `running` runs, then sets them to `interrupted` |
| `resume_run(id) -> bool` | `interrupted` → `running` |
| `ping() -> bool` | `SELECT 1` |

- `update_run` keeps raising `LookupError` for an unknown run (M1 test).

**Approval rows and decisions** (T2)
- Pause (`_pause(run_id, values, interrupts) -> str`): `pause_run` with one call per interrupt (`interrupt.value` is `{tool_call_id, tool, args}`). None (the run was cancelled meanwhile) → emit nothing and return the run's current status, which the segment returns. Otherwise return `awaiting_approval` after one `approval` event per row, as in M1 (node `approval`, status `pending`, attention `warn`, msg `create_incident waits for a person's approval`), with data `{tool_call_id, tool, args, interrupt_id, approval_id, expires_at, run_error}`. `run_error` is the state's `error`: `max_tool_calls` when a call blocked in the same reply already ends the run after this call (M1 handoff), else null.
- One approval at a time (owner, round 1). The approval node calls `interrupt()` once per call, in reply order, and LangGraph stops at the first call without a value. So each pause has exactly one interrupt and one `pending` row. Each decision resumes the graph; the node runs again, gets the earlier values back by index and pauses at the next call. The tools step runs after the last decision. The node does not change. With the default `max_incidents_per_run: 1` a reply never holds two approvals; it takes a config change.
- `decide(run_id, approval_id, *, decision, reason=None, args=None, actor) -> dict`, in this order:
  1. `get_approval`; missing, or its `run_id` differs → `NotFound`.
  2. Approval not `pending`, or run not `awaiting_approval` → `Conflict` (`approval <id> is <status>`, `run <id> is <status>`). This check comes before any wait, so a decision on a final run (even one in online evaluation) fails at once.
  3. `edit`: `tool_gateway.check_input(TOOLS[tool], args)`; a refusal → `ValueError(<its message>)` (422; the row stays `pending`). The stored args are the validated model's `model_dump(mode="json")`.
  4. `decide_approval` with status `approved`, `rejected` or `edited`; the `decision` column holds the edited args, else null; `reason` as given. None → `Conflict` (another decision or a cancel won).
  5. Emit the `approval` event: status = the new approval status; attention `info` for `rejected`, `edited` and `expired`, none for `approved`; msg `create_incident approved by <actor>`, `create_incident rejected by <actor>: <reason>`, `create_incident edited by <actor>` or `create_incident approval expired`; data `{approval_id, tool_call_id, decision: <resume value>, decided_by}`. Then the audit event (`decide_approval`, entity = approval id).
  6. Return the approval. `decide` does not run the graph: the CLI and the tests then await `continue_run`; the API spawns it (T3, T4).
- Resume value (`resume_value(row)`, a module function): what `interrupt()` returns into `decisions`, which the tools node reads.

| Approval status | Resume value |
|---|---|
| `approved` | `{"decision": "approve"}` |
| `edited` | `{"decision": "edit", "args": <row decision>}` |
| `rejected` | `{"decision": "reject", "reason": <row reason>}` |
| `expired` | `{"decision": "reject", "reason": "approval expired"}` |

- Tools node (loop.py): a call whose decision is `reject` never reaches `execute`. Its envelope is `err("rejected", f"{name} was rejected: {reason}")`; `tool_gateway.refused` emits its one attempt-0 `tool` event; no counter changes. `approve` and `edit` go to `execute` as in M1 (the gateway applies edited args and validates them again). The gateway still answers `blocked` to a reject decision (defence in depth; `test_incident_needs_decision` unchanged).
- `tool_gateway._attention`: `rejected` → `info`, like the spec's blue "rejected by the operator".
- No expiry check at decision time: a decision after `expires_at` but before the sweep is accepted (spec: the condition is `status = 'pending'`).

**Which entry runs the graph** (T2)
- `_segment(run, input, llm_client)` is M1's body of `run_segment` from the context to the pause or finish, with `input` passed to `ainvoke`. `run_segment` (the first segment; still refuses a started run) and `continue_run` both call it. Pass `context=RunContext(...)` on every `ainvoke`, resumes included: the context is not checkpointed.
- `continue_run(run_id, *, llm_client=None) -> str` runs every later segment (after a decision, an expiry or a resume):
  1. Unknown run → `NotFound`. Status not `running` → return it without running (a cancel won).
  2. `snap = await graph.aget_state(config)`.
  3. No checkpoint (`snap.values` empty: the process died before the first step) → input = the initial state.
  4. Paused at an approval (`snap.interrupts` not empty): the row for `snap.interrupts[0].value["tool_call_id"]`. Decided → `Command(resume=resume_value(row))`. Missing or still `pending` (the process died between the checkpoint and `pause_run`) → `_pause(run_id, snap.values, snap.interrupts)` and return `awaiting_approval` without calling the graph.
  5. Otherwise → input `None`: LangGraph continues after the last saved step (a finished graph returns its final state at once, and `_finish` writes the row).
- The decision is in the approval row before the graph resumes, so a crash between the decision and the resume loses nothing: the next resume applies it.
- `_finish` writes the final row with `finish_run`. False (a cancel won) → no `done`, no evaluation; return the current status.

**Per-run lock and background tasks** (T3)
- `spawn(run_id, fn) -> asyncio.Task`: `fn` is a zero-argument async callable (a `functools.partial`), so a task cancelled before it starts leaves no un-awaited coroutine. The task runs `async with self._locks[run_id]: await fn()`. One `asyncio.Lock` per run id, made on first use and kept (`ponytail:` comment: one small lock per run id for the life of the process). Tasks sit in `self._tasks[run_id]` (a set) until they end; a done callback removes them and logs any exception except `CancelledError` on `app.runner`.
- `start(run_id)` = `spawn(run_id, partial(self.run_segment, run_id))`.
- Only the API and the sweep spawn. The CLI and the runner tests await `run_segment` and `continue_run` directly.
- A spawned segment holds its run's lock from start to finish, online evaluation included. `decide` and the sweep's expiry take the lock around steps 4 and 5 (the write and the events), after their fast checks; so their events always come after the pausing segment's `approval` event.
- `close()`: cancel every task, `await asyncio.gather(*tasks, return_exceptions=True)`, then close both connections. A cancelled segment writes nothing more; its run stays `running`, and the next API start marks it `interrupted` (ADR 0013).

**Cancel** (T3) `cancel(run_id, *, actor) -> dict`
- From the T1 review: `store.cancel_run` returns 0 (falsy) for a run with no pending approvals, so test `is None` for "not cancelled". A caller cancelled while its shielded transaction waits for the lock still commits later: `CancelledError` does not mean nothing was written.
1. Unknown run → `NotFound`.
2. `cancel_run(run_id, decided_by=actor)`; None → `Conflict` (`run <id> is already <status>`). The 409 comes from the run row, so a final run still in online evaluation is never cancelled and never gets a second `done` (M2 handoff).
3. Cancel every task of the run and `await asyncio.wait(tasks)` (it does not raise their `CancelledError`).
4. Under the run's lock: counters from the checkpoint into the row (`update_run(steps=..., tool_calls=...)`), the audit event (`cancel_run`), then the one `done` (status `cancelled`, attention `info`, data `{status, error: null, steps, tool_calls}`). No evaluation.
5. Return `{run_id, status: "cancelled"}`.
- Cancel also stops a `running` run (the Postman "Cancel" request says so). It can land inside an approved `create_incident`: the incident may exist while the run is `cancelled`.

**Expiry sweep** (T3)
- `sweep() -> int`: for each `expired_approvals(now_iso())` row, under the run's lock: `decide_approval(status="expired", decision=None, reason="approval expired", decided_by="system")`. None → skip (decided or cancelled meanwhile). Otherwise the `approval` event (status `expired`, attention `info`) and the audit event (actor `system`, `expire_approval`), then `spawn(run_id, partial(self.continue_run, run_id))`. Returns how many it expired.
- `sweep_forever(interval_s)`: loop forever: `await self.sweep()` (any `Exception` is logged on `app.runner`), then `await asyncio.sleep(interval_s)`. It sweeps once at start, so approvals that expired while the API was down are handled at once. `CancelledError` ends it.
- Only the API lifespan runs it, every `cfg.approval.sweep_s`. The CLI never sweeps.
- Tests: `monkeypatch.setattr(cfg.approval, "ttl_s", 0)` and call `sweep()` directly.

**Recovery and resume** (T3)
- `recover() -> list[str]`: `mark_interrupted()`, one warning per run on `app.runner` (`run interrupted by a restart; resume it by hand`). No event. Runs in `awaiting_approval` are untouched: their pause is saved.
- Only `main.lifespan` calls it, before the app serves requests. `Runner.open` never does, so the CLI never recovers.
- `request_resume(run_id, *, actor) -> dict`: unknown → `NotFound`; `resume_run` false → `Conflict` (`run <id> is <status>, not interrupted`); audit `resume_run`; returns `{run_id, status: "running"}`. The API then spawns `continue_run`; the CLI awaits it.
- A resumed run repeats the step that was in flight when the process died; only `create_incident` is protected (idempotency key; ADR 0013).

**Audit events** (T2–T6)
- `audit(run_id, actor, action, entity_id)`: kind `log`; no node, tool, status or attention; msg `<actor> <action> <entity_id>`; data `{actor, action, entity_id}`; `run_id` null for an action that is not about one run (M1's `test_audit_event_has_null_run_id`).

| Action | Actor | Entity | Emitted by | Task |
|---|---|---|---|---|
| `create_run` | caller | run id | API create route and CLI `run`, after `create_run` and before the first segment | T4, T6 |
| `decide_approval` | caller | approval id | `decide` | T2 |
| `expire_approval` | `system` | approval id | `sweep` | T3 |
| `resume_run` | caller | run id | `request_resume` | T3 |
| `cancel_run` | caller | run id | `cancel` | T3 |
| `start_eval` | caller | `cfg.eval.golden_set` | eval route and CLI `eval` (`run_id` null) | T5, T6 |

- Each audit event comes before the events it causes, so `done` stays the last event of a run (online evaluation's `eval` events aside). The Postman trace check relies on it.
- The CLI writes the same events with `current_user()`, because it calls the same runner methods.

**API** (T4, T5)
- Wiring: `app/api/__init__.py` has `get_runner(request)`, which reads `request.app.state.runner`; tests override it. Routers in `api/runs.py`, `api/eval.py`, `api/meta.py`, prefix `/api`.
- Lifespan (`main.py`), in order: `Runner.open(settings.db_path)` into `app.state.runner`; `await runner.recover()`; the M2 ingest (same `try` and warning); `sweep = asyncio.create_task(runner.sweep_forever(cfg.approval.sweep_s))`; `yield`; then cancel and await the sweep task, and `await runner.close()`.
- Errors: `NotFound` → 404 and `Conflict` → 409, through exception handlers in main.py (`NotFound` subclasses `LookupError`, so M1's `run_segment` test still holds). The create and decide routes catch `ValueError` → 422 (a pydantic `ValidationError` described with `tool_gateway.describe`, any other `ValueError` as `str(exc)`). Any other exception → 500 `{"detail": "internal error"}` (a handler for `Exception`; the traceback goes to the `app.api` log only). Every error body is `{"detail": ...}`; FastAPI's own 422 keeps its list form.
- Masked responses: `MaskedJSONResponse(JSONResponse)` replaces every `settings.secrets()` value, in plain and JSON-escaped form (`log.secret_forms`), with `***` in the rendered body; `FastAPI(default_response_class=MaskedJSONResponse)`. Needed because the run row and the checkpoint keep an objective as typed (AC-11: no secret in responses).
- Approver token (`auth.require_approver`, only on the decide route): when `settings.approver_token` is set, `hmac.compare_digest(header.encode(), token.encode())` must hold, else 401 `{"detail": "missing or wrong X-Approver-Token"}`. Bytes, because `compare_digest` refuses a non-ASCII `str`. FastAPI solves dependencies before it validates the body, so the 401 comes before 404, 409 and 422 for well-formed JSON (malformed JSON still gets 422 first). The CLI calls `decide` directly and needs no token.
- `current_user()` returns `"anonymous"`.
- CORS: unchanged (`CORS_ORIGINS`, all methods and headers, no credentials).
- Body models use `extra="forbid"` (the `Strict` base in config.py).

| Route | Rules | Task |
|---|---|---|
| `POST /api/runs` | Body `{objective: str, llm?: "fake" or "openai", options?: object}`. The content rules stay in `create_run` (objective 1–2000, limits, faults, `ALLOW_FAULT_INJECTION`); its `ValueError` → 422 and no row. Then audit `create_run`, then `runner.start`. 202 `{run_id, status: "running"}` | T4 |
| `GET /api/runs` | `limit` 1–100, default 20 (422 outside); `list_runs` | T4 |
| `GET /api/runs/{id}` | `run_detail` (below); 404 | T4 |
| `GET /api/runs/{id}/trace` | `{run_id, status, events}`: every stored event of the run in `seq` order (audit and `eval` events included); 404 | T4 |
| `GET /api/approvals` | `status` optional, one of the six approval statuses (422 otherwise); oldest first | T4 |
| `POST /api/runs/{id}/approvals/{approval_id}` | Token check. Body `{decision, reason?, args?}`: `reject` needs `reason` (1–500 characters, not blank); `edit` needs `args` (an object); `args` with `approve` or `reject` → 422; `reason` is kept as a note on the others. `decide(..., actor=current_user())`, then `spawn(run_id, partial(runner.continue_run, run_id))`. 200 the approval | T4 |
| `POST /api/runs/{id}/resume` | `request_resume`, then spawn `continue_run`. 202 `{run_id, status: "running"}`; 404; 409 | T4 |
| `POST /api/runs/{id}/cancel` | `cancel`. 200 `{run_id, status: "cancelled"}`; 404; 409 | T4 |
| `GET /api/tools` | `[{name, description, input_schema, requires_approval}]` in registry order; `input_schema` = `input_model.model_json_schema()` | T4 |
| `GET /api/incidents` | The spec's fields only: `{id, run_id, title, description, severity, status, created_at}` (no `idempotency_key`) | T4 |
| `GET /api/runs/{id}/events` | "Live events" below | T5 |
| `POST /api/eval/kb`, `GET /api/eval/kb/latest` | "Evaluation endpoints" below | T5 |
| `GET /api/health` | "Health" below | T5 |

- Run detail (`Runner.run_detail(run_id) -> dict or None`, used by the API and `cli show`): every run column (`id, objective, status, llm_mode, model, options, final, error, steps, tool_calls, created_at, updated_at, finished_at`), plus:
  - `messages`: the checkpoint's messages (`[]` before the first step).
  - `calls`: from the run's `tool` events, grouped by `data.tool_call_id` in first-seen order: `{tool_call_id, tool, args, attempts, duration_ms, status, result}`. `attempts` = the highest `data.attempt` (0 = refused before running); `duration_ms` = the sum; `args`, `status` and `result` from the last event (`args` are what ran, edited args included). A call that never reached the tools step is not listed; its approval is.
  - `approvals`: `list_approvals(run_id=...)`.
  - `evals`: `store.list_evals(run_id)`.
  - `usage`: `{prompt_tokens, completion_tokens}`, sums over the run's `llm` events; exactly these two keys (the Postman demo checks the key set).
  - `steps` and `tool_calls` are the row's values (written at each pause and at the end).

**Live events (SSE)** (T5)
- FastAPI's native SSE: `@router.get("/runs/{run_id}/events", response_class=EventSourceResponse)` on an async generator that yields `ServerSentEvent(data=event, id=str(event["seq"]))`. No event name, so a browser `EventSource` gets every message in `onmessage`. `data` is the event JSON (`seq, run_id, t_ms, kind, node, tool, status, attention, msg, data, created_at`), the same dicts as `/trace`.
- 404 and the header check happen before streaming: the run comes from a dependency (`existing_run`, 404), and `last_event_id: Annotated[int | None, Header(ge=0)] = None` (a bad value → 422).
- The generator:
  1. Subscribe first (`tracer.subscribe`), so no event falls between the replay and the live part.
  2. Replay `list_events(run_id, after_seq=Last-Event-ID or 0)`; stop after sending `done`.
  3. No `done` in the replay, and the run has a `done` event with `seq <= Last-Event-ID`: the client already has it; end the stream (an `EventSource` that reconnects after `done` gets an empty stream).
  4. Live: `await queue.get()`; skip events with `seq` at or below the last replayed one (already sent); send the rest in arrival order; stop after `done`.
  5. `finally`: unsubscribe.
- The stream never sends anything after `done`. Online evaluation's `eval` events come later; they are in `/trace` and the run detail (spec).
- The generator never changes an event dict: the tracer shares one dict between subscribers (M1 reviewer note).
- Keep-alive is FastAPI's: a `: ping` comment after 15 s without a message, plus `Cache-Control: no-cache` and `X-Accel-Buffering: no`. Nothing to build. A client that leaves is noticed at the next write (at most one ping later); FastAPI's task group then cancels the generator.
- A run in `awaiting_approval` or `interrupted` keeps its stream open, with pings, until a segment continues it.

**Evaluation endpoints** (T5)
- `POST /api/eval/kb` with `response_class=EventSourceResponse`. Body optional: `{modes?: list of "hybrid", "dense", "sparse", at least one}` (`extra="forbid"`); duplicates dropped, order kept; default `golden.MODES`.
- 503 before streaming: a dependency calls `qdrant.get_kb()` and `await kb.status()`; an exception or `unavailable` → 503 `{"detail": "knowledge base unavailable"}`, and no audit event.
- The generator: audit `start_eval` (run_id null, entity `cfg.eval.golden_set`); a task runs `golden.run_golden(kb, metrics.get_judge(), runner.store, modes=..., progress=...)`, where `progress` puts `{done, total}` on a queue. The generator yields `ServerSentEvent(event="progress", data={done, total})` per item, then `ServerSentEvent(event="report", data=report)` and ends. An exception in the job is logged on `app.eval` and sent as `event="error"` with `{"detail": "evaluation failed"}`.
- A client that disconnects stops the evaluation: the generator's `finally` cancels the job; no report is stored (owner, round 1).
- `GET /api/eval/kb/latest`: `store.latest_eval_report()`, else 404 `{"detail": "no evaluation report yet"}`.

**Health** (T5; owner, round 1)
- Body; HTTP is always 200; `status` is `degraded` only when the database does not answer:
  ```
  {status: "ok" or "degraded", llm_default,
   db: {ok}, llm: {model, reachable}, embeddings: {model, reachable},
   qdrant: {reachable}, judge: {model, reachable},
   kb: {mode: "hybrid", "sparse_only" or "unavailable"}}
  ```
- The probes run together (`asyncio.gather`). Each gives false on any exception and is capped by `asyncio.timeout(2.0)` (embeddings: `cfg.kb.embed_timeout_s`):
  - `db`: `store.ping()`.
  - `llm`: `runner.llm_reachable()`, on the runner's one `OpenAICompatClient`, whose new `reachable()` does `self._sdk.with_options(timeout=2.0, max_retries=0).models.list()` (the judge's probe, M2). Model = `settings.llm_model`.
  - `judge`: `metrics.get_judge().reachable()`; model = `get_judge().model`.
  - `qdrant`: `qdrant.get_kb().client.get_collections()` answers.
  - `embeddings`: `kb.embedder.embed(["ping"])`; model = `kb.embedder.model` (`settings.embed_model` when `get_kb()` fails).
  - `kb.mode`: `kb.status()`; `hybrid` becomes `sparse_only` when the embeddings probe failed (AC-14); `get_kb()` failing → `unavailable`.
- No URLs, keys or error texts in the body.

**CLI** (T6)
- `app/cli.py` uses `argparse` (ADR 0001). `async def amain(argv) -> int` does the work (tests await it); `def main(argv=None) -> int: return asyncio.run(amain(argv))`; `if __name__ == "__main__": sys.exit(main())`. It opens `Runner.open(settings.db_path)` and closes it in `finally`. No recovery, no sweep, no background tasks: it awaits each segment.
- Logging: `log.setup("WARNING", "text", settings.secrets())` and the `app.trace` logger disabled: the CLI prints every event itself, and the JSON trace lines would double them.

| Command | Does |
|---|---|
| `run "<objective>" [--llm fake or openai] [--faults JSON] [--max-steps N] [--no-eval]` | Options from the flags: `--faults` through `json.loads` (bad JSON → exit 2), `--max-steps` → `limits.max_steps`, `--no-eval` → `evaluate: false`. `create_run` (a `ValueError` → message on stderr, exit 2; `ALLOW_FAULT_INJECTION` applies there); audit `create_run`; print the run id; then the loop below |
| `list [--limit N]` | One line per run: id, status, created_at, objective cut to 60 characters; newest first; default 20 |
| `show <run_id>` | `run_detail` as indented JSON; unknown → exit 1 |
| `resume <run_id>` | `request_resume` (`NotFound` or `Conflict` → message, exit 1), then the loop below with `continue_run` |
| `ingest [--force]` | `ingest(qdrant.get_kb(), settings.data_dir / "kb", force=...)`; prints the result as JSON; any exception → message, exit 1 |
| `eval [--modes hybrid,dense,sparse]` | The API's 503 check (message, exit 1); audit `start_eval`; `run_golden` with a `progress` that prints `done/total`; prints each mode's summary as JSON |

- The loop (run and resume): subscribe to the tracer; a printer task prints one line per event (`seq`, kind, node or tool, msg, and the attention in brackets when set); await the segment; then cancel the printer and print what is left in the queue, so no event is lost or reordered. `awaiting_approval` → the prompt, `continue_run`, and again. A final status → print it and the final answer.
- The prompt shows the tool, the args as JSON and `expires_at`, then asks `[a]pprove / [r]eject / [e]dit: ` with `input()`. `r` asks `Reason: ` until it is not blank. `e` asks `Arguments as JSON: `; bad JSON, or the `ValueError` that `decide` raises for invalid args, prints the message and asks again. Any other answer asks again. End of input (`EOFError`) → print `approval <id> left pending`, exit 1. A `Conflict` from `decide` (decided elsewhere, cancelled, or expired by an API sweep) → print it, exit 1.
- Exit codes: 0 when the command did its job (for `run` and `resume`: the run ended `completed`); 1 when the run ended in another status or the command failed (unknown run, conflict, approval left pending, knowledge base down); 2 for bad arguments or input (argparse's own code).
- `ingest(kb, docs_dir, *, force=False)`: `force` skips the "unchanged" check and rebuilds (M2 handoff: the hash does not cover `cfg.bm25` or the tokenizer). The rebuild keeps M2's rule: the embedder's reply is checked before the old collection is deleted.
- `cli run` waits for online evaluation, because `run_segment` includes it (M2 handoff).

**Test fixtures and helpers**
- T3, conftest: `async def wait_for_status(runner, run_id, *statuses, timeout=5.0) -> dict` polls `store.get_run` every 0.02 s and fails the test on timeout. Tests import it like `SECRET`.
- T4, conftest: autouse `tmp_db_path` sets `settings.db_path = tmp_path / "harness.db"`: the lifespan now opens a runner, and no test may touch `data/harness.db` (the `runner` fixture uses the same file). `api` fixture: requests `runner`, sets `app.dependency_overrides[get_runner]`, yields `httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://test")`, then clears the overrides. It does not run the lifespan; the `runner` fixture's teardown (`close()`) cancels spawned segments.
- T4: lifespan tests enter `main.lifespan(main.app)` directly (`async with`) around an `httpx2.AsyncClient`, without overrides.
- T5, conftest: autouse `no_real_llm` makes `OpenAICompatClient.reachable` return false, so no test reaches a real chat endpoint. In test_api.py: `parse_sse(text)` (split on blank lines; `:` lines are comments; `id:`, `event:` and `data:` fields) and `wait_subscribed(runner, run_id)` (polls `runner.tracer._subscribers`).
- SSE tests: `httpx2.ASGITransport` hands back a response only after the app ends it. So start the request as a task (`asyncio.create_task(api.get(...))`), wait until the stream has subscribed, drive the run to `done` (for example, approve), then await the task inside `asyncio.timeout(5)`.
- Keep-alive test: `monkeypatch.setattr(fastapi.routing, "_PING_INTERVAL", 0.05)`. `fastapi.routing` imports the name, so patching `fastapi.sse` does nothing.
- API tests use `llm: fake` only: an `openai` run would reach the network from a background task.

**Tests changed on purpose** (they pinned an earlier milestone's state; the change is this milestone's behaviour, not a weaker test)
- T5 `test_skeleton.py::test_health`: `with TestClient(app) as client:`, because the health route now reads the runner that the lifespan opens; the assertion (`status == "ok"`) stays.
- No other existing test changes. Checked: the pause tests in `test_loop.py` (the `approval` event data only gains keys), `test_tools.py::test_incident_needs_decision` (the gateway still blocks a reject decision), `test_tracing.py::test_one_done_event_per_run` (no audit event at runner level), `test_eval.py` (online evaluation after `done`, `_finish` wins its conditional write), `test_kb.py::test_startup_*` (the lifespan also opens a runner, on the tmp DB from T4's autouse fixture; health probes are faked by `no_real_kb`, `no_real_judge` and, from T5, `no_real_llm`).

## Library notes (checked 2026-09-27 on PyPI, the upstream source at the locked versions and the official docs)

**No new package.** Everything below is already in `uv.lock`. T5 turns `"fastapi"` into `"fastapi>=0.135.0"` in `pyproject.toml` with `uv add` (native SSE arrived in 0.135.0) and commits `uv.lock`; `sse-starlette` is not needed.

**FastAPI 0.141.1 (Starlette 1.7.0)**
- `from fastapi.sse import EventSourceResponse, ServerSentEvent`. A path operation declared with `response_class=EventSourceResponse` that `yield`s is streamed as SSE, with any HTTP method (POST included).
- `ServerSentEvent(data=..., event=..., id=..., retry=..., comment=...)`: `data` is always JSON-encoded (`jsonable_encoder`); `raw_data` sends text as is; `id` and `event` must be one line. A yielded plain object becomes `data:` with no `id`.
- FastAPI solves dependencies (and validates parameters) before it calls the generator, so a dependency's `HTTPException` gives a normal 404 or 503 and a bad header a 422. The generator body starts only when the response streams.
- Keep-alive: `: ping` after `_PING_INTERVAL = 15.0` s without a message, via `anyio.fail_after` around the stream in `fastapi.routing`, which imports the constant by name. The docs call it not configurable; tests patch `fastapi.routing._PING_INTERVAL`. Headers `Cache-Control: no-cache`, `X-Accel-Buffering: no`.
- The generator runs in a producer task inside an anyio task group entered on the request's exit stack; when the response ends or the client leaves, the task group is cancelled and the generator's `finally` runs.
- The request body is parsed before dependencies: malformed JSON gets 422 even on a route with a 401 dependency.
- ruff `B008`: use `Annotated[..., Depends()]` (FastAPI's recommended style) instead of calls in defaults.

**httpx2 2.13.1 (dev dependency; Pydantic's continuation of httpx; import `httpx2`)**
- `httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://test")`. `ASGITransport` awaits the app to completion and returns the collected body, so an SSE request returns only after the stream ends. It does not run the lifespan. `raise_app_exceptions=False` lets a test see a 500 response instead of the exception.
- Starlette 1.7.0's `TestClient` imports `httpx2` (and warns if it falls back to `httpx`); it also collects the whole body, so an endless stream hangs it. `with TestClient(app)` runs the lifespan.

**uvicorn 0.54.0**
- `Server.shutdown` stops accepting, then waits for open connections with `timeout_graceful_shutdown` (default None: forever), and only then runs the lifespan shutdown. An open SSE stream on a paused run would hold the process. Run with `--timeout-graceful-shutdown 5`; a second `Ctrl+C` also forces the exit.

**LangGraph 1.2.12** (M1 notes still hold)
- `from langgraph.types import Command`. With one pending interrupt, `Command(resume=value)` makes that `interrupt()` return `value`.
- Several `interrupt()` calls in one node: on resume the node starts from its first line, and LangGraph matches resume values to calls strictly by index, from a list kept per task. Only the first call without a value raises, so our approval node pauses with one interrupt at a time.
- Invoking with `None` after an `interrupt()` re-runs the node and pauses again; invoking with `None` otherwise continues after the last saved step.
- `StateSnapshot.interrupts` (`await graph.aget_state(config)`) holds the pending interrupts (`Interrupt(value, id)`); `values` is empty before the first checkpoint.
- Pass `context=` on every `ainvoke`, resumes included (not checkpointed).

**aiosqlite 0.22.1 and SQLite**
- One connection = one worker thread running queued statements in order. Cancelling an `await` does not remove the statement from the queue: it still runs.
- `BEGIN IMMEDIATE` takes the write lock at once (other connections wait up to their busy timeout: the store's 5 s, and the checkpointer's `sqlite3` default of 5 s). `INSERT ... ON CONFLICT (run_id, tool_call_id) DO NOTHING` needs the UNIQUE constraint the schema already has.

**Python 3.12 stdlib**
- `hmac.compare_digest(a, b)` takes two `str` (ASCII only; `TypeError` otherwise) or two bytes-like objects: compare `.encode()` values.
- `asyncio.shield`, `asyncio.Lock` (not re-entrant), `asyncio.wait` (returns without raising the tasks' exceptions), `functools.partial`, `argparse`.

## Risks
- LangGraph must hand back the first resume value when a node with two `interrupt()` calls resumes the second time (documented: a per-task list matched by index). `test_two_approvals_in_one_reply_asked_in_order` proves it. If it fails, stop and ask; the fallback is to route `approval → approval`, so each node run holds one `interrupt()` and stores its decision in the state before the next.
- An SSE test hangs if its run never reaches `done`: always await the stream task inside `asyncio.timeout(5)`.
- One store connection for all coroutines: without the write lock a statement from another coroutine joins an open transaction; without the shield a cancelled caller leaves a transaction open and SQLite reports "database is locked". Both have T1 tests.
- The per-run lock lives in one process. The API and the CLI on one database do not share it: a CLI run decided through the API continues in the API process (the CLI prompt then gets a conflict), and starting the API while a CLI run is `running` marks it `interrupted`. Listed in DESIGN limitations (T3).
- A cancel during an approved `create_incident` can leave an incident on a `cancelled` run; a person approved it, and the idempotency key still prevents a duplicate.
- One test patches a private FastAPI name (`fastapi.routing._PING_INTERVAL`); a FastAPI upgrade may move it. The test then fails loudly; the lock file pins 0.141.1.
- `GET /api/health` can take up to 2 s when an endpoint hangs; `POST /api/runs` still probes the judge (up to 2 s) when `evaluate` is omitted (M2).
- Real time in tests: the approval-wait test sleeps 1.1 s (limits are at least 1 s); expiry uses `ttl_s = 0` and a direct `sweep()`; keep-alive uses a 0.05 s ping.

## Proof
Each task's tests pass at its own commit.

| AC | Tests | Task |
|----|-------|------|
| AC-1 (API) | `test_api.py::test_create_run_returns_202` (202 `{run_id, status}`; listed by `GET /api/runs`), `::test_invalid_body_returns_422` (unknown field, empty and blank objective, 2001 characters, unknown `llm`, limit below 1, unknown limit key, unknown fault key and mode, `options` not an object; `GET /api/runs` stays empty), `::test_faults_refused_when_disabled` | T4 |
| AC-1 (CLI) | `test_cli.py::test_cli_run_visible_in_api`, `::test_cli_faults_refused_when_disabled` (exit 2, no run) | T6 |
| AC-3 (no approval) | `test_approval.py::test_invalid_incident_args_ask_no_approval` (`SEV9`: no approval row, no `approval` event, `validation` envelope) | T2 |
| AC-4 | `test_api.py::test_run_detail_has_history` (FakePlanner, approve: objective, status, options, messages in order, `calls` with args, attempts, duration and result, the approval, `evals`, `usage` with exactly two keys; 404 for an unknown id), `::test_run_detail_survives_restart` (close the runner, open a new one on the file: the same detail) | T4 |
| AC-8 (approval wait) | `test_limits.py::test_approval_wait_not_counted` (`max_run_seconds: 1`, pause, sleep 1.1 s, approve: `completed`) | T2 |
| AC-9 (pause) | `test_approval.py::test_incident_not_created_before_approval` (`awaiting_approval`, one `pending` row with `expires_at` = `created_at` + `ttl_s`, no incident) | T2 |
| AC-9 (pause, API) | `test_api.py::test_list_pending_approvals` (`?status=pending` lists it with every field; unknown status → 422) | T4 |
| AC-9 (approve) | `test_approval.py::test_approve_creates_one_incident` | T2 |
| AC-9 (reject) | `test_approval.py::test_reject_sends_reason_to_llm` (the scripted LLM's last input holds a `rejected` envelope with the reason; no incident; the call's `tool` event is `rejected` with attention `info`) | T2 |
| AC-9 (edit) | `test_approval.py::test_edit_uses_new_args`, `::test_invalid_edit_keeps_approval_pending` | T2 |
| AC-9 (edit, API) | `test_api.py::test_invalid_edit_returns_422` (422, still `pending`; a valid edit then gives 200 `edited`) | T4 |
| AC-9 (second decision) | `test_approval.py::test_second_decision_conflicts`, `::test_concurrent_decisions_one_wins` | T2 |
| AC-9 (second decision, API) | `test_api.py::test_second_decision_returns_409` | T4 |
| AC-9 (expiry) | `test_approval.py::test_expired_approval_rejects` (`ttl_s` 0, `sweep()`: `expired`, `decided_by` `system`, `approval` event `info`, audit event; the run resumes as rejected with `approval expired` and ends without an incident) | T3 |
| AC-9 (cancel) | `test_approval.py::test_cancel_closes_pending_approval` (`cancelled` run and approval, a later decision conflicts, the sweep does not resume it, no incident, one `done` with attention `info`) | T3 |
| AC-9 (cancel, API) | `test_api.py::test_cancel_final_run_returns_409` (200 then approval `cancelled`, decision 409, second cancel 409, unknown 404) | T4 |
| AC-9 (token) | `test_api.py::test_approver_token_required` (missing or wrong → 401, right → 200; 401 before a body error; no token needed when unset) | T4 |
| AC-10 (cap across replies) | `test_approval.py::test_incident_cap_blocks_without_approval` (approve the first incident; the next reply's `create_incident` gets `blocked` with no second approval) | T2 |
| AC-11 (trace, usage) | `test_api.py::test_trace_export`, `::test_usage_sums_llm_tokens` (scripted replies with 12 and 5 tokens) | T4 |
| AC-11 (audit) | `test_api.py::test_audit_event_for_state_changes` (create, decide, resume, cancel: `log` events with actor `anonymous`, action and entity id) | T4 |
| AC-11 (audit, evaluation) | `test_api.py::test_eval_start_is_audited` (`run_id` null) | T5 |
| AC-11 (secrets) | `test_api.py::test_secrets_never_in_responses` (a configured secret in the objective: absent from list, detail, trace and approvals; `***` present) | T4 |
| AC-11 (SSE) | `test_api.py::test_sse_replays_then_streams`, `::test_sse_resumes_after_last_event_id` (also `Last-Event-ID` at `done`: empty stream; `abc`: 422), `::test_sse_closes_after_done` (online evaluation on: the stream ends at `done`, the `eval` events are only in `/trace`), `::test_sse_sends_keep_alive` | T5 |
| AC-12 | `test_api.py::test_tools_listed` | T4 |
| AC-14 (health) | `test_api.py::test_health_reports_kb_mode` (`kb` fixture: `hybrid`; failing embedder: `sparse_only`; no knowledge base: `unavailable`) | T5 |
| AC-15 (API) | `test_api.py::test_eval_endpoint_streams_report` (fake judge: progress up to the total, then the report; `/latest` returns it), `::test_eval_without_kb_returns_503`, `::test_latest_report_404_when_none` | T5 |
| AC-16 (runner) | `test_recovery.py::test_awaiting_approval_untouched`, `::test_continue_run_resumes_from_last_checkpoint` | T3 |
| AC-16 (API) | `test_recovery.py::test_running_becomes_interrupted_on_startup` (lifespan stopped during a hanging tool, started again), `::test_resume_finishes_run` (then `POST /resume`: 202 and a final status); `test_api.py::test_resume_non_interrupted_returns_409` | T4 |
| AC-16 (CLI) | `test_cli.py::test_cli_does_not_recover` | T6 |
| AC-17 (demo flow) | Manual: with `uv run uvicorn app.main:app --port 8000` running, `npx newman run docs/postman_collection.json --folder "1. Demo flow: create → approve → trace"` passes (needs Node; M5 makes it part of the proof) | T4 |

Spec rules without an acceptance criterion of their own:
- T1: `test_store.py::test_pause_run_writes_row_and_status_together` (and None when the run is not `running`; a second pause of the same call reuses the row), `::test_decide_approval_is_conditional` (second decision, run not awaiting, cancelled run: None), `::test_cancel_run_closes_pending_approvals` (final run: None), `::test_expired_approvals_only_for_awaiting_runs`, `::test_mark_interrupted_and_resume_run`, `::test_finish_run_only_from_running`, `::test_list_runs_newest_first`, `::test_list_approvals_filters`, `::test_cancelled_transaction_leaves_no_open_transaction`, `::test_writes_wait_for_an_open_transaction` (a write from another coroutine is not rolled back with a failed transaction).
- T2: `test_approval.py::test_two_approvals_in_one_reply_asked_in_order` (`max_incidents_per_run` 2: one pending row per pause, no tool event before the second decision, one incident), `::test_resume_duplicates_no_event_or_row`, `::test_mixed_reply_runs_all_calls_after_approval`, `::test_approval_event_has_id_expiry_and_run_error` (`max_tool_calls` 1 with an incident then a status call: `run_error`, then `limit_exceeded` with the incident), `::test_decide_emits_approval_and_audit_events`.
- T3: `test_lifecycle.py::test_spawned_segments_of_one_run_run_one_at_a_time`, `::test_decision_waits_for_the_pausing_segment` (the pending `approval` event held back: `decide` waits, then its events follow it), `::test_cancel_running_segment_writes_one_done` (hanging tool: `cancelled`, one `done`, nothing after it), `::test_cancel_during_online_evaluation_conflicts` (slow judge: 409, one `done`, evaluation still stored), `::test_close_cancels_running_segments` (the run stays `running`, no `done`), `::test_sweep_forever_keeps_going_after_an_error`; `test_recovery.py::test_resume_applies_decision_recorded_before_crash`, `::test_resume_repauses_when_approval_row_missing`, `::test_request_resume_needs_interrupted`.
- T4: `test_api.py::test_list_runs_newest_first_and_limit` (the eight summary fields; `limit` 0 and 101 → 422), `::test_decision_body_rules` (reject without or with a blank reason, a 501-character reason, `args` with approve, unknown field, unknown decision → 422), `::test_decision_on_unknown_or_mismatched_ids_returns_404`, `::test_incidents_listed` (no `idempotency_key`), `::test_unexpected_error_gives_json_500` (no exception text), `::test_cors_allows_configured_origin_only`.
- T5: `test_api.py::test_sse_unknown_run_returns_404`, `::test_eval_body_rejects_unknown_mode`.
- T6: `test_cli.py::test_cli_interactive_approve`, `::test_cli_reject_and_edit_validate_input`, `::test_cli_eof_leaves_approval_pending`, `::test_cli_list_show_resume`, `::test_cli_exit_codes`, `::test_cli_run_waits_for_online_evaluation`, `::test_cli_ingest_and_eval`; `test_kb.py::test_ingest_force_rebuilds`.

## Doc changes
Each note goes in the commit of the task that builds the behaviour. The spec can be edited as a doc of the task; these lines only define what it left open.
- T2, `specs/ops-agent-harness.md`, Approval, after "`edit.args` replaces the arguments completely.": "- A reply with several calls that need approval asks for them one at a time, in reply order; the tools step runs after the last decision." and "- A decision is stored in the approval row before the run continues, so a resume after a crash applies it." (owner, round 1)
- T2, same file, Events and logs, after the sentence on audit events: "Actions: `create_run`, `decide_approval`, `resume_run`, `cancel_run`, `start_eval`; the expiry sweep writes `expire_approval` with actor `system`. The CLI writes the same events." Attention table, after "Call edited or rejected by the operator": a row "Rejected call (its `tool` event) | `tool` | `info` | blue".
- T3, `specs/ops-agent-harness.md`, Run lifecycle: the last diagram line becomes `running | awaiting_approval | interrupted ──cancel──► cancelled`; the cancel bullet gains "A running segment is stopped first; the run then gets its one `done` event."
- T3, `docs/DESIGN.md` §8, new bullet: "**One process drives a run.** The per-run lock lives in memory. A CLI run decided through the API continues in the API process, and starting the API while a CLI run is `running` marks it `interrupted`."
- T3, `docs/adr/0013-recovery-interrupted-runs.md` status line: "· Refined in M3: a decision stored before a crash is applied on resume ([spec, Approval](../../specs/ops-agent-harness.md#approval))". Index row in `docs/adr/README.md`: "Accepted (decisions survive a crash, M3)".
- T4, `specs/ops-agent-harness.md`, API, under the table: "- Decisions: `reason` (1–500 characters) is required for `reject`; `args` (an object) is required for `edit` and refused with the other decisions. With `APPROVER_TOKEN` set, the token check comes first." "- `resume` answers 202 and `cancel` 200, both with `{run_id, status}`. `GET /api/approvals` lists the oldest first." "- An unexpected error answers 500 `{"detail": "internal error"}`. Configured secret values are masked in every response."
- T4, `docs/DESIGN.md` §4: row "Side effect without consent" gains "`X-Approver-Token` is compared in constant time"; row "Leaking secrets" becomes "Secret values masked in logs, events and API responses".
- T5, `specs/ops-agent-harness.md`, API, under the table: "- `/events`: each message has `id: <seq>` and `data: <event JSON>`, with no event name. The stream ends after the `done` event, or at once when `Last-Event-ID` is at or past it. Keep-alive is a `: ping` comment." "- `POST /api/eval/kb` sends `event: progress` with `{done, total}`, then `event: report` with the report (`event: error` if it fails). A client that disconnects stops the evaluation." (owner, round 1) "- `/api/health` answers `{status, llm_default, db: {ok}, llm: {model, reachable}, embeddings: {model, reachable}, qdrant: {reachable}, judge: {model, reachable}, kb: {mode}}`; `status` is `degraded` when the database does not answer; each probe waits at most 2 s." (owner, round 1)
- T5, `docs/DESIGN.md` §8, new bullet: "**Live streams and shutdown.** uvicorn waits for open connections before it stops; the run command passes `--timeout-graceful-shutdown 5`, so an open event stream cannot hold it."
- T5, `README.md` (Run) and `docs/REVIEW_GUIDE.md` §1: the uvicorn command gains `--timeout-graceful-shutdown 5`.
- T6, `specs/ops-agent-harness.md`, CLI, the second bullet becomes: "`list [--limit N]`, `show <run_id>`, `resume <run_id>`, `ingest [--force]` (rebuild even when unchanged), `eval [--modes hybrid,dense,sparse]`." and a new bullet: "Exit code 0 when the command worked (for `run` and `resume`: the run completed), 1 otherwise, 2 for bad arguments or input. At an approval prompt, end of input leaves the approval pending."
- No Postman change. Checked every request against these rules: create gives 202 with `run_id`; the demo polls to `awaiting_approval`; pending approvals carry `id`, `run_id` and `tool`; incidents carry `run_id`; approve gives 200 `status: approved`, and the empty `X-Approver-Token` header is ignored while `APPROVER_TOKEN` is unset; `usage` has exactly two keys; the trace is in `seq` order and ends with one `done` (the demo sets `evaluate: false`, and audit events come before `done`); `{"objective": "", "unknown_field": true}` gives 422; the tool names match; the cancel and resume descriptions match.
- No new environment variable or config key: DESIGN §7, `.env.example` and `config.yaml` do not change. ADRs 0009, 0011 and 0014 already say what M3 builds. `docs/REVIEW_GUIDE.md` rows already use the test names in Proof.

## Handoffs
- M4 (UI):
  - Live events: `new EventSource('/api/runs/{id}/events')`; each message's `data` is one event (`onmessage`). Close the `EventSource` on the `done` event, or the browser reconnects every few seconds and gets empty streams. Evaluation badges: re-read `GET /api/runs/{id}` after `done` (M2).
  - Approval inbox: `GET /api/approvals?status=pending`, updated by `approval` events (status `pending`, `approved`, `rejected`, `edited`, `expired`) and by a `done` event with status `cancelled` (a cancel closes the run's approvals without an `approval` event). `expires_at` drives the TTL countdown. `data.run_error = "max_tool_calls"` on a pending event means the run ends after this call: say so. A run has at most one pending approval at a time.
  - Decisions: `POST /api/runs/{id}/approvals/{approval_id}`, with `X-Approver-Token` when the server sets `APPROVER_TOKEN` (a UI setting). 401, 404, 409 and 422 bodies are `{"detail": ...}` (FastAPI's own 422 has a list); a 422 on edit keeps the approval pending.
  - `POST /api/eval/kb` streams over POST, which `EventSource` cannot send: use `fetch` and read the `event:` and `data:` lines.
  - Timeline from the run detail's `calls`; budget meters from the guard `stage` data (M1); a status indicator from `GET /api/health`.
- M5 (ship):
  - Docker command: `uvicorn app.main:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 5`.
  - `evals/run.sh`: create the run, poll to `awaiting_approval`, take the run's pending approval, send the next entry of `decisions`, and repeat per pause (one approval at a time); poll to a final status; save `/trace`. `check.sh`: attempts from `tool` events (`data.attempt`), status from the `done` event, incidents from `ok` `tool` events of `create_incident`.
  - The review guide takes the test names from this Proof table.

## Open questions
None. In round 1 (2026-09-28) the owner accepted all three defaults: several approvals in one reply are asked one at a time, in reply order; `GET /api/health` returns the nested body with every dependency probed at once, the chat LLM included; a golden-set evaluation whose client disconnects stops and stores no report. All three are written into the rules above (marked "owner, round 1").

## Pipeline log
| Phase | Result | Notes |
|-------|--------|-------|
| Phase 1 planner | NEEDS_ANSWERS | Checked against the spec, ADRs 0001, 0004, 0009, 0011, 0013, 0014 and 0017, the M1 and M2 handoffs and pipeline logs, the API standard, all 32 Postman requests and the current code. Split 4 tasks into 6 (store, decisions, lifecycle, API core, streams with evaluation and health, CLI): 5.5h (+0.5h); plans/README.md updated. Pinned: store write lock and shielded transactions (one shared connection); decisions stored before the resume, so `continue_run` rebuilds `Command(resume=...)` from the approval row and a crash never loses one; per-run lock and background tasks; cancel with one `done`; the sweep; recovery only in the API; SSE replay, live, `Last-Event-ID` and close rules; API shapes, error mapping and masked responses; health; CLI and exit codes. Library notes: FastAPI 0.141.1 native SSE (no new package), httpx2 2.13.1 and Starlette 1.7.0 test clients collect the whole body, uvicorn 0.54.0 waits for open streams at shutdown, LangGraph 1.2.12 interrupts. No Postman change. 3 open questions |
| Phase 1 round 1 | READY | Owner, 2026-09-28: all three defaults accepted (one approval at a time; nested health body with the chat LLM probe; an evaluation stream that disconnects stops with no report). Markers changed to "owner, round 1"; Open questions: none. The planner was cut off by a rate limit before this step; the orchestrator applied it. plans/README.md: the stale M2 T4 cut removed |
| Gate 1 | Approved | Owner, 2026-09-28 |
| T1 build | done | Skills: `ai-engineer` invoked. `store.py`: write lock on every write, `_transaction` (`BEGIN IMMEDIATE` under the lock, shielded), `list_runs`, `finish_run`, `pause_run`, `get_approval`, `approval_for_call`, `list_approvals`, `decide_approval`, `expired_approvals`, `cancel_run`, `mark_interrupted`, `resume_run`, `ping`; approvals in API shape. 318 tests, 2 live skipped |
| T1 tester | PASS | 324 tests, 2 live skipped; added pause rows in call order, a failing pause rolls back the status too, cancel of an interrupted run closes its approval, expiry boundary `expires_at == now`, a second decision never moves the run, parsed `args` and `decision` |
| T1 reviewer | REQUEST CHANGES, fixed | MAJOR fixed: every read is one aiosqlite job (`execute_fetchall`); a cursor read over several jobs held a WAL snapshot, and a commit on the checkpointer's connection then made the next store write fail with "database is locked" (reviewer stress probe: up to 2% of writes). Regression test `test_reads_do_not_break_writes_from_another_connection` fails 3 of 3 runs with the old reads. MINOR fixed: `close()` takes the write lock (queued transactions finish first); `COMMIT` inside the `try`, so a failed commit is rolled back. NITs: empty-transaction return in `pause_run` kept; two notes added to the T3 Cancel rules. 326 tests, 2 live skipped |
| T1 re-review | APPROVE | All findings checked as fixed; docstring nit fixed (`execute_fetchall` allowed inside `work`). 326 tests, 2 live skipped |
