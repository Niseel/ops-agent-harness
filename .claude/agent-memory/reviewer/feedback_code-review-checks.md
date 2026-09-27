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
