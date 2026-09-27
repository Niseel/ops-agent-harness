---
name: harness-code-review-checks
description: Checks that found real issues when reviewing M1 harness code (log masking, store SQL, tracer, FakePlanner arg limits, openai client); run them on any change to log.py, store.py, tracer.py, llm/ or gateways
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

**Why:** the tester runs the listed tests; these gaps pass them.
**How to apply:** on every review touching logging, SQL or the tracer, run the probes above in the scratchpad. See also [[spec-adr-config-drift-hotspots]].
