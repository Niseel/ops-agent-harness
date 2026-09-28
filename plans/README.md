# Roadmap

How the work in [specs/ops-agent-harness.md](../specs/ops-agent-harness.md) is split. One plan per milestone, one branch and one pull request per plan, one commit per task.

| Plan | Branch | Scope | Tasks | Estimate | Status |
|---|---|---|---|---|---|
| [m0-foundation](m0-foundation.md) | `chore/m0-foundation` | Skeleton, ADRs, workflow, intent and spec, plans, design doc, Postman, review guide | 8 | 6.5h | done |
| [m1-harness-core](m1-harness-core.md) | `feat/m1-harness-core` | State, store, tracer, LLM clients and gateway, tool gateway, policy, loop and runner | 6 | 7.5h | done |
| [m2-kb-and-eval](m2-kb-and-eval.md) | `feat/m2-kb-and-eval` | Hybrid knowledge base search, golden-set and per-run evaluation | 4 | 5.5h | done |
| [m3-approval-api-cli](m3-approval-api-cli.md) | `feat/m3-approval-api-cli` | Approval decisions, REST API with live events, recovery, CLI | 6 | 5.5h | done |
| [m4-ui](m4-ui.md) | `feat/m4-ui` | Run console UI | 6 | 5.5h | done |
| [m5-ship](m5-ship.md) | `chore/m5-ship` | Docker, CI, scenario evals, final docs and review guide | 5 | 3.5h | draft |
| **Total** | | | **35** | **34h** | |

## Where each acceptance criterion is built

| AC | Milestone | AC | Milestone |
|---|---|---|---|
| AC-1 | M3 | AC-10 | M1 (idempotency), M3 (incident cap) |
| AC-2 | M1, completed in M2 | AC-11 | M1 (events, logs), M3 (SSE, audit) |
| AC-3 | M1, M3 (no approval asked) | AC-12 | M1 (tools), M2 (search tool), M3 (`GET /api/tools`) |
| AC-4 | M1, M3 (API) | AC-13 | M4 |
| AC-5 | M1 | AC-14 | M2, M3 (health) |
| AC-6 | M1 | AC-15 | M2, M3 (endpoints) |
| AC-7 | M1 | AC-16 | M3 |
| AC-8 | M1, M3 (approval wait) | AC-17 | M0, M5 |
| AC-9 | M1 (pause, no incident), M3 (approval rows, decisions) | AC-18 | M5 |

## How a milestone runs

0. The owner runs `/feature plans/<file>.md` ([the skill](../.claude/skills/feature/SKILL.md)); the planner subagent checks and updates the plan.
1. **Gate 1**: the owner approves the milestone plan (status becomes `approved`).
2. For each task, in order:
   - invoke the skills in the task's Skills column;
   - write the code and tests;
   - run the commands in [CLAUDE.md](../CLAUDE.md);
   - run the `tester` and `reviewer` subagents and fix what they find;
   - add a row to the plan's Pipeline log;
   - **gate 2**: the owner approves, then the agent commits (`Plan: plans/<file>.md T<n>` in the body).
3. The owner pushes the branch, the agent opens the pull request, the owner reviews and merges (rebase and merge).

## If time runs short

The estimate (34h) is above the 2–3 day budget. Cut in this order, and list the cut items as limitations in `docs/DESIGN.md`:

1. M4 T6, Evaluation and Incidents tabs (the golden-set report and the incidents stay available through Postman and the CLI; AC-15 keeps its API tests). This replaced "M4 T6, frontend specs" when each M4 task took its own specs (planner, 2026-09-28; confirm at gate 1).
2. M5 T1, Docker image (run instructions stay host-based, and FastAPI does not serve the built UI).

Both cuts save 1.25h, down to 32.75h. M2 T4 (online evaluation) was on this list; M2 is merged, so it is built.
