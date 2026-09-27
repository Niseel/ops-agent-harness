---
name: plan-review-checks
description: Checks that caught real gaps when reviewing plans/m*.md milestone plans (task files, dependencies, invariants between milestones)
metadata:
  type: feedback
---

When reviewing milestone plans, check these first (found in the M0 T5 review, 2026-09-27):

- Shared helpers (harness/retry.py, tools/faults.py) owned by one task but needed by a task marked parallel or earlier. Every file a task needs must be created by that task or by one of its dependencies.
- An endpoint an AC needs, with no task that lists `api/*` in its Files (for example `POST /api/runs/{id}/resume` fell between M3 T2 and T3). Map each route in the spec's API table to a task.
- A harness invariant broken at an intermediate commit. For example, M1 builds `create_incident` and the loop before M3 adds approval. Each milestone must leave REVIEW.md invariants true.
- Files listed in "Files that change" that no task lists, or that two milestones both claim.
- The AC→milestone table in plans/README.md must match each plan's Proof table and task AC tags.

**Why:** coding agents follow the task row literally. Gaps there turn into missing features or invariant breaks that the per-task review then has to catch.
**How to apply:** for every plan review, walk the spec's API table and AC list, then tick off each item against a task's Files column and a Proof row.
