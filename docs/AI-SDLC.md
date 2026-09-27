# AI-Native SDLC: how this repo works

The process is a loop, not a line. Each stage commits an artifact the next stage reads.

1. Plan     -> intent/*.md   (what and why)                  skill: /intent
2. Design   -> specs/*.md    (requirements + design)         skill: /spec
3. Build    -> plans/*.md + code (plan first)                skill: /feature
4. Test     -> tests + evals/* (the agent verifies itself)   agents: tester, verifier
5. Deploy   -> PR + REVIEW.md findings (human approves)      agent: reviewer + CI
6. Maintain -> not used here: this project has no deployment (see ADR 0018)

## Roles
- planner: writes plans, never edits code.
- coder: implements the approved plan, fixes failures.
- tester: writes and runs tests from acceptance criteria, only edits test files.
- reviewer: read-only review against REVIEW.md and the plan.
- verifier: runs the app in a fresh context to confirm behaviour.

## Guardrails (see .claude/)
- guard-bash.sh: blocks destructive commands (rm -rf outside the repo, push, sudo...).
- protect-tests.sh: blocks edits to test files during a fix task (set FIX_MODE=1).
- production-gate.sh: pauses production deploys until a named approval.
- Formatting and lint are checked by ruff in CI, not by a hook.

## Skills
Skills are installed with `npx skills` into `.agents/skills/` (pinned in `skills-lock.json`) and linked into `.claude/skills/` (Claude Code) and `.aider-desk/skills/` (AiderDesk) so each tool finds them.

| Skill | Used for | Loaded by |
|---|---|---|
| ai-engineer | LLM, agent loop, tools, knowledge base, evaluation | planner, coder, tester, reviewer (preloaded); main agent when a task lists it |
| secure-api-review | anything under `/api`, the Postman collection | coder, tester, reviewer, verifier (preloaded); main agent when a task lists it |

Subagent models: planner `opus`/max, reviewer `opus`/high, coder `sonnet`/xhigh, tester `sonnet`/medium, verifier `haiku`/low.

Each plan task names its skills in the Skills column. The main agent invokes them before coding the task and notes them in the Pipeline log.

## Human stays above the loop
Approve the plan (gate 1) and the commit (gate 2). Nothing pushes automatically.

## In this project

| Stage | Artifacts |
|---|---|
| Plan | intent/ops-agent-harness.md |
| Design | specs/ops-agent-harness.md (requirements R*, acceptance criteria AC-*), docs/adr/, docs/DESIGN.md |
| Build | plans/README.md (roadmap), plans/mN-*.md (one per milestone, each task = one commit) |
| Test | backend/tests/, frontend specs, evals/*.json scenarios graded by evals/check.sh |
| Deploy | one PR per milestone (.github/pull_request_template.md), CI in .github/workflows/ci.yml |

How a milestone runs (the owner starts it with `/feature plans/mN-*.md`):
1. The planner subagent checks the plan against the spec, ADRs and code. Gate 1: the owner approves it.
2. For each task: the main agent writes code and tests, the tester subagent checks the acceptance criteria, the reviewer subagent reviews the diff, and both verdicts go into the plan's Pipeline log. Gate 2: the owner approves, then the agent commits.
3. The owner pushes the branch, the agent opens the PR, the owner reviews and merges.

The main agent writes the code itself instead of delegating to the coder subagent: it already holds the context, and the tester and reviewer still give an independent check. `.claude/skills/feature/SKILL.md` is adapted for this and for one commit per task.

M0 ran the stages by hand: `intent/ops-agent-harness.md` and `specs/ops-agent-harness.md` were written by hand in the template format, from the brief and the owner's answers, instead of through `/intent` and `/spec`. `/intent` and `/spec` are for changes after the first release.

Re-running `ai-sdlc-starter --adopt` refreshes the managed blocks in CLAUDE.md and AGENTS.md and brings back the bilingual text.
