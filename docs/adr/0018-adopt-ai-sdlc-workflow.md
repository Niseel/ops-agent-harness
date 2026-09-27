# 0018. Adopt the AI-SDLC workflow

Status: Accepted · Date: 2026-09-27

## Context

After the first ADRs, the task plan lived in one document outside the repo. Reviewers and coding agents need the same artifacts for every change: why, what, how, and proof. An AI-SDLC starter kit was added to the repo: intent → spec → plan → test → review, five subagents (planner, coder, tester, reviewer, verifier), guard hooks and two human gates.

## Options

| Option | Pros | Cons |
|---|---|---|
| One plan document (`docs/PLAN.md`) | Simple. | No link from requirement to task to test. Agents have no fixed format to follow. |
| The kit as delivered | Ready-made. | Bilingual text. Maintenance-stage stubs (monitoring bands, rollback runbook, incident template) that do not apply and clash with the product's own runbooks and incidents. An example API policy that requires authentication this project does not have. |
| The kit, trimmed to this project | Traceable IDs, human gates, guard hooks. | Some adaptation work. |

## Decision

Adopt the kit, trimmed:

- **Artifacts**: `intent/ops-agent-harness.md` (why), `specs/ops-agent-harness.md` (requirements `R*`, acceptance criteria `AC-*`), `plans/` with one file per milestone (tasks `T*`, each task is one commit) and `plans/README.md` as the roadmap. This replaces the `docs/PLAN.md` planned in [0017](0017-docs-postman-ci.md).
- **Stage 6** (maintain: monitoring bands, incidents, rollback runbook) is dropped. There is no deployment in scope.
- **evals/** holds end-to-end scenario evals, graded by `evals/check.sh` from a run's trace, and the retrieval golden set.
- **Roles**: the main agent writes code and docs. The tester and reviewer subagents check each task before its commit; their verdicts go into the plan's Pipeline log.
- **Git**: one branch per milestone (`<type>/<slug>`), Conventional Commits, one pull request per milestone. The agent commits and opens pull requests; a human pushes, reviews and merges. Hooks block `git push` and destructive commands. See CONTRIBUTING.md.
- **API policy**: the `secure-api-review` skill holds this project's real standard (validation, clamped limits, audit events, no secrets), without authentication.
- **Formatting**: ruff runs in CI instead of a format-on-save hook.
- All text is in English.

## Consequences

- Every requirement can be traced: `R` → `AC` → plan task → test → review guide row.
- Each task passes two human gates (plan, commit) and one CI run per pull request.
- Re-running `ai-sdlc-starter --adopt` refreshes the managed blocks in CLAUDE.md and AGENTS.md and brings back the bilingual text.
