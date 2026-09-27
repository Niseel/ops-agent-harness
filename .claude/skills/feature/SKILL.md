---
name: feature
description: Run one milestone plan (or one change) through plan review, per-task implementation, tests, review and commit, with two human gates. The main agent writes the code; subagents plan, test and review.
argument-hint: "[plans/<file>.md | specs/<file>.md | intent/<file>.md | free text]"
disable-model-invocation: true
---
# Feature pipeline

You are the ORCHESTRATOR. You write the code, tests and docs of each task yourself
(ADR 0018, CONTRIBUTING.md). Subagents give independent checks: planner (the plan),
tester (acceptance criteria), reviewer (the diff), verifier (the running app).
Delegate coding to the coder subagent only when the user asks for it.
Talk to the user in the language they use.

Request: $ARGUMENTS

## Ground rules
- Subagents start EMPTY. Every delegation includes the plan path, the task id, its
  acceptance criteria and Proof rows, and any failures or findings verbatim.
  Never say "see above".
- Before coding a task, invoke every skill in its Skills column.
- After each phase, append one row to the plan's Pipeline log: skills invoked,
  verdicts, commit hash.
- One task = one commit on the milestone branch.
- Never push, merge, deploy, or rewrite pushed history.

## Phase 1 - Plan
- Argument is a plan: delegate to planner to check it against the spec, the ADRs
  and the current code, and to update it.
- Argument is a spec, an intent or free text: planner writes plans/<slug>.md from
  plans/_TEMPLATE.md.
- STATUS: NEEDS_ANSWERS -> ask the user with AskUserQuestion (planner's default
  first) and send the answers back. Max 3 rounds.

## GATE 1 - Human approves the plan (REQUIRED)
Show the plan path, tasks with estimates, acceptance criteria and risks.
Ask Approve / Request changes / Cancel. On Approve, set the plan's Status to
`approved`. Do not continue without Approve. On changes -> planner -> repeat.

## Phase 2 - Branch
If the tree is dirty, ask the user. Otherwise switch to the plan's branch:
`git switch -c <branch>` from an up-to-date main, or `git switch <branch>` if it exists.
If the previous milestone's pull request is not merged yet, ask the user whether to
branch from it or to wait.

## Phase 3 - One task at a time, in the plan's order
1. Invoke the task's skills. Implement the task (code, tests, docs). Run the
   commands in CLAUDE.md.
2. Tester (max 3 rounds): delegate with the task's ACs and Proof rows.
   FAIL with PRODUCT_BUG -> fix the code -> tester again. Never weaken a test.
3. Reviewer (max 2 rounds): delegate with the plan, the task id and the base
   (the previous commit). BLOCKER/MAJOR -> fix -> tester (regression) -> reviewer.
4. Verifier (optional) for user-facing tasks. `RESULT: MISMATCH` is handled like a
   tester FAIL: fix -> tester -> reviewer.
5. GATE 2 (REQUIRED): report files changed, AC status, command output, verdicts
   and leftover nits. Ask Commit / Request changes / Stop. On Commit: a
   Conventional Commits message whose body ends with `Plan: plans/<file>.md T<n>`,
   with no `Co-Authored-By` or "Generated with" lines (CONTRIBUTING.md).
   After the commit, stop until the owner says to continue; then the next task.

After 3 failed test rounds or 2 unresolved review rounds: stop and ask the user.

## Phase 4 - Wrap up
When every task is committed: set the milestone's status in plans/README.md to
`done`, use the PR title from the plan's header line,
write the PR description from .github/pull_request_template.md (AC table, check
output, per-task verdicts) and ask the user to push the branch. After the push,
open the PR with `gh pr create`. Do NOT push.
