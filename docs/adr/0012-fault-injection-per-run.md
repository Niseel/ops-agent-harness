# 0012. Fault injection per run

Status: Accepted · Date: 2026-09-26 · Added 2026-09-27: `embeddings` fault key (mode `error`) to force `sparse_only` search ([spec, Fault injection](../../specs/ops-agent-harness.md#fault-injection))

## Context

Timeouts, retries, bad outputs and malformed replies must be shown in the demo and covered by tests, the same way every time.

## Options

| Option | Pros | Cons |
|---|---|---|
| Per-run switches in the request | Deterministic. Same scenario in tests, UI and Postman. | One more request field. |
| Magic inputs in fixtures (for example, service `flaky-db` always times out) | No API change. | Depends on the LLM picking that input. Cannot trigger on demand. |
| Random failure rate | Realistic noise. | Not deterministic; flaky tests. |

## Decision

`POST /api/runs` accepts `options.faults`:

```json
{
  "get_service_status": {"mode": "timeout", "times": 2},
  "llm": {"mode": "malformed", "times": 1}
}
```

- Tool modes: `timeout`, `error`, `bad_output`, `latency`, `timeout_after_commit` (the incident is created, then the call times out).
- LLM modes: `malformed`, `timeout`.
- The injector sits inside the tool gateway and the LLM gateway, so the real validation and retry code handles the fault.
- Faults hit the first `times` attempts. Counting uses the run's attempt counters in the checkpoint, so behaviour is the same after a resume.
- Faults are refused unless `ALLOW_FAULT_INJECTION=true`.

## Consequences

- Every failure scenario can be replayed from a test, the UI switches or a Postman request.
- Production deployments set `ALLOW_FAULT_INJECTION=false`.
