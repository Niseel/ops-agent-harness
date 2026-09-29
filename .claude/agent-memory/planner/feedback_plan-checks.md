---
name: plan-checks
description: Checks that found real gaps when the planner reviewed milestone plans (M1–M3 phase 1, 2026-09-27; M4 UI and M5 ship phase 1, 2026-09-28; M6 UI upgrade, 2026-09-29); run them on every plans/mN-*.md review
metadata:
  type: feedback
---

Gaps found in M1 phase 1 (2026-09-27). Check these first on every milestone plan:

- A Proof test that needs code from a later task. Gateway tasks listed run-level tests ("run completes", "run fails") that need the loop. Fix: split unit tests (gateway task) from run-level tests (loop task) and tag each Proof row with the task that writes it.
- A shared file needed earlier than the task that owns it. `prompts/system.md` sat in the loop task, but the LLM gateway hashes it for `prompt_sha`. Same for conftest fixtures: put shared fixtures in the first task, so a task marked "parallel" still has them if it moves earlier.
- Hidden dependencies between "parallel" tasks. The LLM gateway needed the tool registry (for unknown-tool checks). Fix by passing data in (tool definitions) instead of importing.
- Cross-milestone file ownership. Later plans often need edits to files an earlier milestone created (store.py queries, registry.py, tool_gateway.py) without listing them. Record them in a "Handoffs" section rather than editing other milestone plans unasked.
- Demo facts pin behaviour. The Postman step-limit demo (fake LLM, max_steps 2 → limit_exceeded) means FakePlanner makes one tool call per reply. Read docs/postman_collection.json and evals/*.json before pinning fake/test-double rules.
- ADR wording can be older than the spec. ADR 0011 says the guard checks `max_tool_calls` before each LLM call; the spec and AC-8 say only a blocked call ends the run. Pin the spec's version in the plan.
- When the owner answers a question about an API-visible value (e.g. `t_ms`, run `error` codes in round 1), check whether the spec leaves it undefined. If it does, give the task that builds it a one-line spec note in a "Doc changes" section; the reviewer's compliance pass reads specs/plans/ADRs, not DESIGN.md. Also look for spec'd values nothing emits (the `error` event kind had no emitter).
- Split any task that mixes a pure-policy module with integration code (M1: policy.py split out of loop/runner; M2: knowledge-base library split from tool + harness wiring). Keep the milestone estimate and update the task count in plans/README.md (also its "If time runs short" task ids).

Added in M2 phase 1 (2026-09-27):
- Existing tests that pinned an earlier milestone's temporary state break by design (a deferred option asserted `is None`, a registry asserted to have two tools, an exact event trail, a test double). List them in the plan under "Tests changed on purpose" with the reason, or the "never weaken a test" rule stalls the coder and the reviewer.
- Grep existing tests for assertions a new feature will disturb: "done is the last event" broke once post-run events were added; a 50 ms `short_timeouts` fixture would race a real tool that emits events.
- A job that must run after an event emitted outside the graph (the runner emits `done`) cannot live in a graph node; also check which exits skip the node (timeouts, recursion, internal errors).
- Resolve new dependencies against the current uv.lock, not in isolation (a `<0.4` pin could not coexist with the langchain-core that langgraph needs), and read the package's open GitHub issues for import-time breaks.
- Check new libraries for phone-home defaults (ragas usage analytics) and pin them off in the plan and DESIGN's safety table.
- Fixture text read by a rule-based fake must be designed against the fake's regexes and every demo/test objective; pin a ranking test over the list of objectives.

Added in M3 phase 1 (2026-09-27):
- "One transaction" in a plan is not atomic if the store shares one aiosqlite connection across coroutines: other coroutines' statements join it. Pin an in-process write lock for every write plus `asyncio.shield` around multi-statement writes (a cancelled await still runs the queued statement, so BEGIN can be left open).
- Make every final-status write conditional (`WHERE status = 'running'`) and emit `done` only when it won: that, not task bookkeeping, is what guarantees one `done` under cancel races.
- Between a DB decision and a graph resume there is a crash window: store the decision first and rebuild the resume value from the row.
- Streaming endpoints: check the test transport (httpx2 `ASGITransport` and Starlette `TestClient` collect the whole body) and the server's shutdown (uvicorn waits for open streams unless `--timeout-graceful-shutdown`).
- Walk the Postman collection request by request against the pinned shapes: exact key sets (`usage`), "last event is `done`" (audit events must come before it), header behaviour when a token is unset.
- ruff rules that bite API and test code: `B008` (FastAPI calls in defaults, use `Annotated`) and `ASYNC110` (a `while` loop that only sleeps).
- A CLI that prints events live doubles its output if the trace logger still writes JSON lines to stdout.
- plans/README "If time runs short" still lists M2 T4 as a cut although M2 is done: report it, do not edit the owner's cut list.

Added in M4 phase 1 (2026-09-28, UI plan):
- Map every field a spec'd panel shows to the event or API field that feeds it, at the moment it must show. `tool` events are emitted after each attempt, so the NOW bar had no args during a first attempt: the fix was one backend line (`args` in the `llm` event's `tool_calls`). Also list what the live stream never sends (`eval` events come after `done`) and where the UI reads it instead.
- Write an "event shapes the UI reads" table from the code (kind, node/tool/status, data keys); the UI rules and the test fixtures both hang on it.
- Framework defaults change semantics: Angular 22 components are OnPush by default and new apps are zoneless, so pin "state in signals only". Pin a CSS `[hidden] { display: none !important; }` when panels toggle with `hidden` and have their own `display`.
- A draft with a final "specs" task: move each spec into the task that builds the behaviour, then fix plans/README's cut list, which may name the removed task.
- Scaffold commands must pass every option so nothing prompts (and `--skip-git`, AI-config off, analytics off).

Added in M5 phase 1 (2026-09-28, ship plan):
- Graders must be falsifiable. For each scenario ask "does its expect block still pass if the fault or decision never fires?" A malformed-reply scenario that only checks `completed`, an injection scenario with no check that an approval was asked, and an incident count read from the trace after `timeout_after_commit` (the first attempt carries no id) all passed vacuously. Fix with checks from the trace (decision events vs the decisions list, `llm` event counts) or data from the real system (incidents API).
- Environment-dependent scenarios: without an embedding endpoint the index is BM25-only, every search is `sparse_only`, and the `embeddings` fault never fires. Say where each scenario really exercises its fault, and run it both ways.
- New scenario or demo objectives: reuse the ones already listed in `test_kb.py` (`PLAIN_OBJECTIVES`), which guard the injected-document ranking; a new objective needs that list updated.
- ADR wording vs handoffs again: ADR 0017 said "backend tests with a Qdrant service container", but pytest uses in-memory Qdrant; the container belongs to an end-to-end job, plus a status note.
- Scripts for macOS: bash 3.2 (`set -u` makes an empty array unbound; no `mapfile`); macOS 15 has `/usr/bin/jq`.
- Docker checks: publish an unauthenticated API (and Qdrant) on 127.0.0.1; a non-root user needs its state directory created and chowned in the image (named volumes copy that ownership); startup work (ingest) needs `depends_on: service_healthy`; the Qdrant image has no curl (bash `/dev/tcp` healthcheck).
- A `StaticFiles` mount at `/` after the routers: GET on a missing path still reaches the JSON 404 handler, but POST gets 405.
- "Clean clone" proof before the commit exists: `git add -A && git archive "$(git write-tree)" | tar -x -C <dir>` exports exactly what the commit will hold.
- CI changes cannot run before the owner pushes: plan a YAML parse, the commands by hand, and a fix commit if the first run fails.
- Agents cannot open `.env.*`: plans must not need an edit to `.env.example`.

Added in M6 phase 1 (2026-09-29, UI upgrade plan):
- UI changes: grep every spec for text and selector assertions first and list them as constraints ("keep green"), not as tests to change. Here: uppercase `NOW` only in the NOW line, no `attempt` while an approval waits, filter buttons found by exact text, the first `aria-hidden` in a row is the icon, exactly 3 meters, `now()` compared with `toEqual` (add a new computed instead of a field).
- jsdom 30.1.1 has no `showModal`/`close`/`scrollIntoView`/`matchMedia` and zero rects and scroll sizes: plan prototype stubs and a pure placement function.
- A frontend spec CAN read a repo file as text: `ng test` builds with the application builder, which supports `import x from '../styles.css' with { loader: 'text' }` (plus `// @ts-expect-error`). So a check of a frontend file stays a frontend spec. I first claimed the opposite without reading `@angular/build`, and the coordinator caught it: before planning around a tool "limit", find it in the installed source.
- The verifier subagent has only Bash and Read: screenshots and no-scroll checks need headless Chrome driven over the DevTools protocol by a scratch Node script (Node's global `WebSocket`).
- A wait in a test double: read the config at call time, zero it in an autouse conftest fixture, patch a module-level `sleep` name; then check e2e wait budgets (`evals/run.sh` 60 s per scenario, newman 40 x 300 ms) and subprocess tests.
- The Skills column must name only invocable skills; a marketplace plugin that is not enabled cannot be listed.

Validated approach (owner accepted every default in M1 and M2 round 1, 2026-09-27, and all four in M5 round 1, 2026-09-28): write each question's recommended default into the rules before asking, and list what each other answer would change. Finalizing is then small: mark the rules "(owner, round 1)", set Open questions to "None" with a one-line record, add a Pipeline log row, and re-check wording that the answers touch (spec notes, handoffs, test details).

**Why:** coding agents follow task rows literally; each task must pass its own tests at its own commit.
**How to apply:** walk every Proof row and ask "which commit makes this test pass?", every file a task imports and ask "which task created it?", and every existing test that touches the changed path and ask "does its assertion still hold?". See also [[library-notes-pointer]].
