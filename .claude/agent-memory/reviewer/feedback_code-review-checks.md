---
name: harness-code-review-checks
description: Checks that found real issues when reviewing M1 harness code (log masking, store SQL, tracer); run them on any change to log.py, store.py, tracer.py or gateways
metadata:
  type: feedback
---

Found in the M1 T1 review (2026-09-27):

- Secret masking done on the finished JSON log line misses secrets that json.dumps escapes (`"`, `\`, control chars): the line holds `tok\"en`, not `tok"en`. Probe it with a quick script (log.setup + logger.error) instead of trusting the tests, which use a plain alphanumeric secret. Fix: also replace `json.dumps(secret)[1:-1]`, or mask before encoding.
- `update_run(**fields)` builds SQL from keyword names; check the column whitelist and a test with an injection-shaped key.
- Store connection: verify PRAGMAs live (journal_mode=wal, busy_timeout, in_transaction False after a write) with a probe, not just by reading.
- Tests that call `log.setup(...)` reconfigure the root logger globally (force=True) and bind it to capsys's stdout; watch for leaked state in later tests.

**Why:** the tester runs the listed tests; these gaps pass them.
**How to apply:** on every review touching logging, SQL or the tracer, run the probes above in the scratchpad. See also [[spec-adr-config-drift-hotspots]].
