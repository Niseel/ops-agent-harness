# 0002. Agent loop: LangGraph engine, our own nodes

Status: Accepted · Date: 2026-09-26

## Context

The loop must save state after every step, pause for a human approval and resume later (even after a restart), stop at limits, retry failed tools and trace every attempt. These policies are the core of the harness, so they must be visible and testable in our code.

## Options

| Option | Pros | Cons |
|---|---|---|
| Hand-written `while` loop | Full control, no dependency. | We would rebuild checkpointing and pause/resume. |
| LangGraph with prebuilt `ToolNode` and `RetryPolicy` | Least code. | `ToolNode` hides validation and timeouts. `RetryPolicy` retries the whole node, so tool calls that already succeeded run again, and attempts do not show in the trace. |
| OpenAI Agents SDK / Claude Agent SDK | Loop, tools and guardrails built in. | Tied to one provider. Hides the parts this project is meant to show. |

## Decision

Use LangGraph `StateGraph` only as the execution engine:

- checkpointer (`AsyncSqliteSaver`) saves state after every step,
- `interrupt()` / `Command(resume=...)` pauses and resumes for approvals,
- `recursion_limit` is a last safety net.

Every node is ours: `guard → agent → approval → tools → finalize`. No `ToolNode`, no `RetryPolicy`. Policies (limits, validation, retries, approval) live in `backend/app/harness/`.

```
START → guard ──limit hit──────────────────────→ finalize → END
          │ ok
        agent ──final answer──────────────────→ finalize
          │──malformed reply──→ guard
          │──needs approval──→ approval (interrupt) ──→ tools
          └──other tool calls──────────────────→ tools ──→ guard
```

## Consequences

- Checkpoints and resumable pauses come for free and are well tested upstream.
- Policies are plain functions, easy to unit test.
- On resume LangGraph re-runs the paused node from its start. Code before `interrupt()` must have no side effects; the approval node only reads state.
- Messages are plain OpenAI-style dicts, not LangChain message objects (see [0003](0003-llm-openai-compatible-with-fake.md)), so LangChain prebuilt helpers do not apply.
