---
name: harness-code-review-checks
description: Checks that found real issues when reviewing M1 harness code (log masking, store SQL, tracer, FakePlanner arg limits, openai client, saver connection on cancel); run them on any change to log.py, store.py, tracer.py, llm/, gateways, runner.py
metadata:
  type: feedback
---

Found in the M1 T1 review (2026-09-27):

- Secret masking done on the finished JSON log line misses secrets that json.dumps escapes (`"`, `\`, control chars): the line holds `tok\"en`, not `tok"en`. Probe it with a quick script (log.setup + logger.error) instead of trusting the tests, which use a plain alphanumeric secret. Fix: also replace `json.dumps(secret)[1:-1]`, or mask before encoding.
- `update_run(**fields)` builds SQL from keyword names; check the column whitelist and a test with an injection-shaped key.
- Store connection: verify PRAGMAs live (journal_mode=wal, busy_timeout, in_transaction False after a write) with a probe, not just by reading.
- Tests that call `log.setup(...)` reconfigure the root logger globally (force=True) and bind it to capsys's stdout; watch for leaked state in later tests.

Found in the M1 T2 review (2026-09-27):

- FakePlanner builds `create_incident` arguments from pieces with separate caps (objective[:1800], snippet[:150]) but left `doc_id` uncapped: a 51-char doc id plus a 2000-char objective gave a 2012-char description. Probe with a long objective AND long uncapped fields; the tests used short ids. A final `[:2000]` cut is the simple fix.
- `AsyncOpenAI(api_key="")` raises `OpenAIError` at construction (only None falls back to env, and empty is rejected). Matters wherever the client is built (T6 runner) if `LLM_API_KEY` is set empty for a keyless local server.

Found in the M1 T3 review (2026-09-27):

- `unique_ids` replaces a missing or reused provider id with `s<step>c<i>` but never checks that the generated id is free: provider ids `("s3c1", None)` at step 3 give `["s3c1", "s3c1"]`, and a history id `s3c0` collides too. The plan rule is written the same way, so the coder follows it. Probe with ids in the harness's own format.
- Attention values an event sets that the spec's attention table does not list (e.g. `llm` + `error` on unavailable) need a spec row, since the M4 UI maps from that table.

Found in the M1 T4 review (2026-09-27), carry into T5/T6/M3 reviews:

- `tool_gateway.execute` accepts an `edit` decision but runs `call["args"]`; it never reads `decision["args"]` (plan: the M3 tools node swaps them in first). In M3, probe that an edit really creates the incident with the edited args.
- `incidents` goes up only on an `ok` envelope. With `timeout_after_commit` times>=2 the incident is committed but the LLM sees `timeout`, so the cap can let a second proposal through. Check T5/M3 count incidents from the store for the run.
- Probe scripts need `PYTHONPATH=.` from backend/ (`uv run python` does not put `app` on the path).

Found in the M1 T5 review (2026-09-27):

- Test names in a task's file can collide with names a later task's Proof rows reserve in the same file (T5 unit `test_limits.py::test_repeat_call_blocked` vs T6's run-level test of that name). Grep the plan's Proof table for every new test name; a later collision invites deleting or overwriting the earlier test.
- pydantic `strict=True` in a model's config does not reach nested models with their own config (options `faults.times: "2"` or `true` still coerce). Probe nested fields when a coder claims "strict".
- `raw or {}` turns any falsy non-dict (`[]`, `0`, `""`) into defaults; probe option parsers with falsy wrong types.

Found in the M1 T6 review (2026-09-27):

- The checkpointer's own aiosqlite connection (default isolation level) keeps an open write transaction when a segment is cancelled between `execute` and `commit` in `AsyncSqliteSaver.aput`/`aput_writes` (segment timeout, M3 cancel). Every store write then fails with `database is locked` after busy_timeout, for every run, until the saver commits again. Probe: `create_task(saver.aput(...))`, cancel after 1 loop tick, check `conn.in_transaction`. Fix: `aiosqlite.connect(db_path, isolation_level=None)` for the saver too. Re-check whenever a new connection or cancel path is added (M3 cancel, M4 SSE).
- LangGraph pulls langsmith through langchain-core: `LANGSMITH_TRACING=true` in the environment would ship run state to LangSmith. Nothing enables it; check it stays documented or forced off.

Found in the M1 owner timestamp fix review (2026-09-27):

- One format rule: every harness timestamp comes from `app/clock.py:now_iso` (`...T09:00:00.123Z`), guarded by a regex test in test_tracing.py. The regex also matches `fromisoformat(` (parsing), so M3 approval expiry should compare `expires_at` as text against `now_iso()` or add a parse helper to clock.py. Fixture data (`data/services.json` `updated_at` has no ms) is outside the rule; check new fixtures and DESIGN §3 wording stay consistent.

Found in the M2 T4 review (2026-09-27), carry into M3/M4:

- Online evaluation runs inside `_finish` after `done`, with the run row already final. M3 cancel must decide 409 from the row status, never from "segment task still running"; otherwise a cancel during evaluation cancels the task and writes a second `done` (`cancelled`). Probe: cancel while a slow FakeJudge scores.
- Spec attention table names only "Judge unreachable" for `eval`+`info`, but every null (error, NaN, no contexts) gives `info`. M4 maps from that table.
- `evaluate_run` probes the judge (up to 2 s) even when there is nothing to score (empty state, no search, no final).

Found in the M3 T1 review (2026-09-28), carry into T3/T4/T5:

- WAL stale snapshot on the shared store connection: a read written as `async with db.execute(...) as cur: await cur.fetchall()` is 2-3 queued aiosqlite jobs. If the checkpointer's connection commits between them, any store write queued in that window (autocommit or `BEGIN IMMEDIATE`) fails at once with `database is locked` (SQLITE_BUSY_SNAPSHOT skips busy_timeout). Stress probe: two coroutines looping `get_run`/`list_events`, one looping writes on a second aiosqlite connection, one looping `insert_event`: 1-6 of 300 writes failed. With reads as one job (`execute_fetchall`) 0 of 300. Re-run this probe whenever a read path or connection changes (API, SSE replay, health).
- `Store.close()` during a shielded transaction: the connection closes mid-transaction (SQLite rolls back; state stays consistent, the shield's promise does not). Fix: `close()` takes `_write` first.

Found in the M3 T2 review (2026-09-28), carry into T3/T5:

- A second `done` needs a final run row set back to `running`; only `decide_approval` (from awaiting) and `resume_run` (from interrupted) set `running`, and no app code calls `update_run(status=...)`. Re-grep these when T3-T6 add writers.
- Row write and its event are two steps: crash between `pause_run` and the `pending` event, or between `finish_run` and `done`, leaves a run with no such event. T5 SSE ends only on `done`: check a final run without `done` does not hang its stream.
- `_internal_error` emits its `error` event before the conditional `finish_run`: when a cancel won, that event lands after the cancel's `done`.
- Multi-interrupt crash probe that paid off (passed): two approvals, decide c0, patch `pause_run` to raise, reopen `Runner.open` on the same file, `continue_run` re-pauses at c1, decide, reopen again, resume: both incidents, c0's edited args kept (LangGraph keeps earlier resume values in the checkpoint by index). Put probe tests in `backend/tests/` temporarily (fixtures), delete after.

Found in the M3 T3 review (2026-09-28), carry into T4/T5/T6:

- Proven (probe passed): cancelling a spawned segment mid-tool cancels the tool too (LangGraph leaves no orphan task); no event lands after cancel's `done`. Probe script: Runner.open on a temp file, hanging tool via `registry.TOOLS[...] = replace(tool, run=hang)`, spawn, cancel, release, sleep, list events. Re-run it if the loop or gateway gains new tasks.
- No-event-after-done rests on: every spawned entry (`run_segment`, `continue_run`) reads the row first, and cancel snapshots `_tasks` right after `cancel_run` commits. Any new spawned fn that emits before reading the row breaks it.
- Per-run lock waits are unbounded: the sweep (stale `expired_approvals` list) or a racing `decide` can wait behind a whole segment (max_run_seconds plus online evaluation). Rare; flagged MINOR.
- `tracer.emit` inserts, then publishes: a segment cancelled between the two leaves a stored event that live subscribers never get (SSE gap). Matters for T5 only.
- T4: `start` after a racing cancel makes `run_segment` raise ValueError, logged as "background segment failed".

**Why:** the tester runs the listed tests; these gaps pass them.
**How to apply:** on every review touching logging, SQL or the tracer, run the probes above in the scratchpad. See also [[spec-adr-config-drift-hotspots]].

Found in the M2 T1 review (2026-09-27), carry into T2/T3/M3:

- `ingest` checks the embedder only by "did it raise": a reply with fewer vectors than chunks raises IndexError after `delete_collection`/`create_collection`, leaving an empty collection that `status()` calls `hybrid`. Probe any rebuild path with a short or empty embedding reply.
- `content_hash` covers docs + embed model only (plan-pinned): a change to `cfg.bm25` or the tokenizer never reindexes, and M3 `cli ingest` has no force flag. Re-check when M3 adds the CLI.
- Lifespan code catches every Exception and logs; a startup test that only checks "no rebuild" passes even when ingest failed. Ask for a caplog assert on the success line.
- Vendor-note margin with the fake embedder (probe script: search each plain objective with limit=24): best rank 5 in sparse, 8 in hybrid. Re-probe if data/kb or the objectives list change.

Found in the M2 T2 review (2026-09-27):

- Output-model caps that mirror a config value (`SearchOutput.results` max 3 vs `cfg.kb.top_n`, no bound in config.py) turn a config edit into `bad_output` on every call. Check each strict output cap against the config key that feeds it; bound the key in config.py or pass the cap explicitly.
- Run-level "never surfaces X" tests that skip non-`ok` envelopes pass vacuously if the tool fails; ask for an assert that at least one `ok` result was checked.

Found in the M2 T3 review (2026-09-27), carry into T4/M3:

- `RagasJudge.metrics()` imports ragas synchronously on the first metric call (~1.5 s warm, more cold). In M3 that stalls the API event loop (SSE, other requests) once per process. Check M3 warms it off the loop (`asyncio.to_thread`) or accepts it in writing.
- Golden search uses `limit=10` sections, but the KB has only 8 docs, so "MRR@10 over the first 10 distinct doc_ids" (spec wording) really means "doc_ids in the first 10 hits". Re-check if the KB or limit changes.
- Probes that paid off: build the metrics with `socket.connect`/`getaddrinfo` blocked (no network, `do_not_track()` True); `uv lock --check` after an override; grep instructor for `jiter` to verify override claims.

Found in the M3 T4 review (2026-09-28), carry into T5/M4:

- `FastAPI(default_response_class=...)` covers route returns only. FastAPI's `RequestValidationError` handler and Starlette's `HTTPException` handler build a plain `JSONResponse`, so a masking response class misses them: the 422 list echoes `input` (an extra field's value, a too-long reason, the whole body for a model_validator error), and `HTTPException(422, describe(exc))` echoes unknown option keys. Probe: set `settings.llm_api_key`, POST a body with the secret as an extra field's value and as an `options` key; grep the body. Fix: register both handlers with the masked class.
- Starlette's `ServerErrorMiddleware` re-raises after an `Exception` handler answers 500; uvicorn's own `uvicorn.error` handler (not the masked root) then prints the traceback with the exception text. Probe: real `uvicorn.Server` in a script with a route that raises `RuntimeError(secret)`; grep stderr. Fix lives in `log.setup` (route uvicorn loggers through the masked handler).
- Real-uvicorn smoke script pattern that works: `uvicorn.Server(Config(app, port=...))` as a task, poll `server.started`, httpx2 client, `server.should_exit = True`; patch `qdrant.get_kb` to raise so startup skips ingest. macOS has no `timeout` command.
- Logging probes must start uvicorn from its CLI (`uv run uvicorn probe_app:app` with a wrapper module in the scratchpad that imports app.main and adds a raising route), in the background: uvicorn configures its loggers before it imports the app, so an in-process `uvicorn.Server` built after importing app.main re-installs the unmasked handlers and gives a false leak. In zsh, `rm -f $DB*` with no match aborts an `&&` chain; list the files explicitly.

Found in the M3 T5 review (2026-09-28), carry into M4 (UI stream) and any SSE change:

- (Fixed in T5 with GRACE_S = 5 s and a regression test; re-check if cancel gains slower steps before `done`, e.g. the unbounded per-run lock wait.) SSE "row final, no `done`" end rule races the writers: `cancel` commits the `cancelled` row, then awaits segment teardown, the per-run lock, `get_state`, `update_run`, the audit event, and only then `done`. A stream opened (or an idle timeout firing) in that window read once more at once and ended with neither the audit event nor `done`. Probe (scratchpad copy of conftest.py + test_api.py, run with `PYTHONPATH=. uv run pytest --rootdir=. -c pyproject.toml <scratch>/test_probe.py` from backend/): patch `runner.get_state` to sleep 0.3, cancel a paused run as a task, `wait_for_status(..., "cancelled")`, then GET /events. Fix shape: a grace deadline of several seconds, reading on every wake until `done` or the deadline (one wake can be the audit event, not `done`).
- Typed int headers/params with only `ge=0` reach SQLite: `Last-Event-ID: 18446744073709551616` raises OverflowError inside the stream (200 sent, stream aborted, ERROR traceback). Check every int that becomes a SQL parameter has `le=2**63-1`.
- Streams bypass MaskedJSONResponse: SSE data is masked only where the code calls `tracer.mask` (dict values, not keys).

Found in the M3 T6 review (2026-09-28), carry into any CLI or interactive change:

- `await asyncio.to_thread(input, ...)` makes Ctrl+C at a prompt hang: asyncio.run cancels the main task, then waits for the executor thread (and the interpreter's atexit join) until stdin yields a line or EOF. A second Ctrl+C hangs too. Probe: `subprocess.Popen([.venv/bin/python, -m, app.cli, run, ...], stdin=PIPE, preexec_fn=lambda: signal.signal(SIGINT, SIG_DFL))`, SIGINT after 6 s, wait 10 s. A shell `&` job ignores SIGINT, and `kill -INT` on `uv run` does not reach the child: both give false results. Fix: a daemon thread that sets a loop future via call_soon_threadsafe.
- Unpacking `[x] = await list(...)` of a row another process can change (API sweep, cancel, decide) raises a bare ValueError traceback. Probe: wrap `Runner.run_segment` so a second `Runner.open` cancels the run right after the segment returns.
- CLI prints are outside the masked response class and the tracer: approval args, `list` objectives, the final answer and exception text print secrets unmasked. Probe with `settings.llm_api_key` set and the secret in the objective; grep stdout.

Found in the M4 T1 review (2026-09-28), carry into every UI task that calls `api()`:

- The frontend `api()` helper throws `ApiError` only for non-2xx answers. A network failure (dev server or uvicorn down) rejects with a raw `TypeError` (no `.detail`), and a 2xx with a non-JSON body resolves to `undefined`. Check each panel's catch shows text for both (`detailText(undefined)` is `JSON.stringify(undefined)` = undefined, not a string). `statusText` is empty over HTTP/2 and in jsdom `new Response` without it.
- TypeScript 6.0 makes `strict` the default, so the Angular 22 `ng new` tsconfig has no `"strict": true`; that is not a missing option (`npx tsc --showConfig -p tsconfig.app.json`).
- Frontend lock check: `node -e` over package-lock `packages` for typescript 6.0.x, resolved host registry.npmjs.org only, and linux-x64 optional bindings present (for the M5 CI job).
