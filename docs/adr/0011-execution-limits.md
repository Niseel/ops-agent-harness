# 0011. Execution limits in layers

Status: Accepted · Date: 2026-09-26

## Context

An agent can loop forever: calling the same tool again and again, failing to produce a valid reply, or waiting on a slow call. One limit alone misses some of these cases.

## Options

| Option | Pros | Cons |
|---|---|---|
| Step limit only | Simple. | A single hung call is not stopped. Fast repeated calls burn the budget. |
| Time limit only | Catches hangs. | A fast loop can do a lot of damage before the time runs out. |
| Token or cost budget | Tracks spend. | Needs token accounting per provider. |
| Several layers | Each case is caught by at least one layer. | More settings. |

## Decision

Layers, all in `config.yaml > limits`:

1. **Steps and tool calls**: the `guard` node checks `max_steps` (LLM turns) and `max_tool_calls` before every LLM call. Over the limit, the run ends as `limit_exceeded`.
2. **Repeat guard**: the same tool with the same arguments more than `max_repeat_calls` times is blocked. The LLM gets a `blocked` error.
3. **Repairs**: `max_repairs` consecutive malformed replies end the run ([0010](0010-failure-handling-envelope-retry-repair.md)).
4. **Wall clock**: `asyncio.timeout(max_run_seconds)` wraps each segment (start or resume). Over the limit, the run ends as `timed_out`. Time spent waiting for an approval is not counted.
5. **Backstop**: LangGraph `recursion_limit`. If it ever fires, the run ends as `limit_exceeded`.

A client may lower limits for one run. Values above the configured limits are clamped.

## Consequences

- No infinite loop even if one layer has a bug.
- There is no token or cost budget yet; token usage is only recorded. Listed as a future improvement.
