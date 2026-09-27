# 0010. Failure handling: result envelope, selective retry, reply repair

Status: Accepted · Date: 2026-09-26 · `max_repairs` means repairs allowed: that many malformed replies in a row are repaired, the next one fails the run ([spec AC-7](../../specs/ops-agent-harness.md#acceptance-criteria))

## Context

Tools time out, fail, or return bad data. The LLM sometimes returns broken replies. The harness must not crash, and the agent should be able to adapt.

## Options

| Option | Pros | Cons |
|---|---|---|
| Fail the run on any error | Simple. | One flaky call kills the run. |
| Retry every error | Simple. | Wastes budget on errors that will not change; risks duplicate side effects. |
| Classify errors: retry transient ones, report the rest to the LLM | Agent can recover. | Needs an error model. |

## Decision

**Every tool result is an envelope:**

```json
{"ok": true,  "data": {...}}
{"ok": false, "error": {"type": "timeout", "message": "...", "retryable": true}}
```

Error types: `validation`, `not_found`, `timeout`, `unavailable`, `bad_output`, `rejected`, `blocked`.

**Tool calls:**

- Input is validated with the tool's Pydantic model (`extra="forbid"`). Invalid input is not executed; the LLM gets a `validation` error and can fix the arguments.
- Each attempt has a timeout (`tools.<name>.timeout_s`).
- Only `timeout` and `unavailable` are retried, up to `tools.<name>.max_attempts`, with exponential backoff and full jitter (`retry.base_delay_s`, `retry.max_delay_s`). Every attempt is a trace event.
- Output is validated with the tool's output model. Invalid output becomes `bad_output` and is not retried; the same system would likely return the same data.
- Output longer than `output.max_tool_result_chars` is truncated.

**LLM calls:**

- API errors and timeouts are retried (`llm.max_attempts`). If all attempts fail, the run ends as `failed` (`llm_unavailable`).
- A malformed reply is one of: `finish_reason = length`, no content and no tool calls, tool arguments that are not JSON, an unknown tool, or more than `limits.max_calls_per_reply` calls.
- A malformed reply is not added to the history. The harness adds a short correction message (`[harness] previous reply invalid: <reason>`) and asks again. After `limits.max_repairs` consecutive malformed replies, the run fails.

## Consequences

- Each error type has its own path and its own test.
- The LLM sees structured errors and often recovers (fixes arguments, tries another tool, or explains the gap).
- Tool calls in one reply run one after another, not in parallel.
