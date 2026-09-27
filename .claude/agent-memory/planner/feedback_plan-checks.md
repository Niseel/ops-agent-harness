---
name: plan-checks
description: Checks that found real gaps when the planner reviewed milestone plans (M1 and M2 phase 1, 2026-09-27); run them on every plans/mN-*.md review
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

Validated approach (owner accepted every default in M1 and M2 round 1, 2026-09-27): write each question's recommended default into the rules before asking, and list what each other answer would change. Finalizing is then small: mark the rules "(owner, round 1)", set Open questions to "None" with a one-line record, add a Pipeline log row, and re-check wording that the answers touch (spec notes, handoffs, test details).

**Why:** coding agents follow task rows literally; each task must pass its own tests at its own commit.
**How to apply:** walk every Proof row and ask "which commit makes this test pass?", every file a task imports and ask "which task created it?", and every existing test that touches the changed path and ask "does its assertion still hold?". See also [[library-notes-pointer]].
