# 0015. UI concept: run console with approval inbox, not a chat

Status: Accepted · Date: 2026-09-26 · Refined in M6: the Runs tab fits one screen, with the Now panel and the flow above the timeline and the console and detail below; one colour per actor (LLM, harness, tool, person) next to the attention colours ([spec, UI](../../specs/ops-agent-harness.md#ui))

## Context

The brief: accept an objective, run the LLM–tool loop, ask for approval before side effects, stop at limits. Reviewers judge the loop, state, failures and safety. The UI should make those visible.

## Options

| Option | Pros | Cons |
|---|---|---|
| Chat box | Familiar to most users. | Suggests a synchronous conversation. Approvals get lost in scrollback. Budget and state are secondary. |
| Run console with approval inbox | Shows steps, state, retries, approvals and budget at once. | New layout. |
| Chat input + timeline, with follow-up objectives | Familiar input. | Adds multi-turn conversations, which the brief does not ask for. |

## Decision

A run console. Tabs: **Runs**, **Evaluation**, **Incidents**.

```
┌ New run ──────┬ Run a1f3 · awaiting · 4/8 ──┬ Approvals (1) ─┐
│ objective [__] │ 1 search_knowledge_base    │ create_incident│
│ LLM [fake v]   │   ok hybrid · 3 hits 180ms │ SEV2 payments  │
│ faults >       │ 2 get_service_status       │ ttl 14:32      │
│ limits > [Run] │   ! timeout, retry 2/3     │ [Approve]      │
├ Runs ──────────┤   ok degraded · 2.1s       │ [Edit][Reject] │
│ * a1f3 waiting │ 3 create_incident(SEV2)    ├ Budget ────────┤
│ v 9c2e done    │   ... waiting approval     │ steps  ####-- │
│ x 77b0 limit   │                            │ calls  ##---- │
└────────────────┴────────────────────────────┴────────────────┘
┌ NOW: get_service_status("payments-api") 2/3 ─────────────┐
│ guard > agent > approval > [kb|status|incident] > final   │
│ 0.41 s2 get_service_status  ! timeout, retry 0.4s  [!]    │
└───────────────────────────────────────────────────────────┘
```

- **Left**: new run form (objective, LLM mode, fault switches, limits, evaluate) and the runs list.
- **Center**: run timeline. Each LLM decision, and each tool call with name, arguments, attempts, result, duration and evaluation badges.
- **Right**: approval inbox for all runs (TTL countdown; approve, edit or reject with a reason), budget meters (steps, tool calls, time), attention list.
- **Bottom**:
  - NOW bar with the running tool, its arguments and attempt number.
  - Flow diagram with one node per tool.
  - Console with filters (All / Tools / Attention).
  - Detail panel with the real data of a step.
- Attention is shown with colour, icon and text:
  - amber: waiting for approval, retry.
  - red: failure, timeout, blocked call, limit hit.
  - orange: invalid input or output, malformed reply, low faithfulness.
  - blue: degraded search, edited or rejected call.
  - green: incident created, run completed.

A run is asynchronous and may wait many minutes for an approval, which a chat layout does not express. The approver is often a different person from the requester, so approvals get their own inbox.

## Consequences

- Harness behaviour is visible at a glance, which matches what is being evaluated.
- There are no follow-up questions in the same thread. Listed as a future improvement.
