<!-- ai-sdlc:start -->
## AI-Native SDLC

This project runs the six-stage loop in docs/AI-SDLC.md. Keep this block:
re-running ai-sdlc-starter --adopt refreshes it and nothing else.

Loop: /intent -> /spec -> /feature. Two human gates: approve the plan, approve the commit.

### Commands that prove a change works
See "Commands" under "Project" below.

### Rules
- Run the commands before reporting a task done, and paste the output.
- If a test fails, fix the code, not the test. Never skip or delete a test.
- Never push, merge or deploy. A human does that.
<!-- ai-sdlc:end -->

## Project: ops-agent-harness

An agent harness for an operations assistant. Why: intent/ops-agent-harness.md. What: specs/ops-agent-harness.md. How: plans/README.md. Decisions: docs/adr/.

### Commands

```bash
cd backend && uv sync --locked            # install
cd backend && uv run pytest -q            # tests
cd backend && uv run ruff check .         # lint
cd backend && uv run ruff format --check . # format (drop --check to fix)
```

Frontend and eval commands are added by the milestone that introduces them.

### Rules
- A milestone starts when the owner runs `/feature plans/mN-*.md`; follow that skill's phases.
- Work one plan task at a time. One task = one commit on the milestone branch. Stop after each commit for human review.
- Before coding a task, invoke every skill in the task's Skills column (Skill tool) and note them in the Pipeline log. Default: `ai-engineer` for LLM, loop, tool, knowledge-base and evaluation work; `secure-api-review` for anything under `/api`.
- Before each commit, run the `tester` and `reviewer` subagents on the task and log their verdicts in the plan's Pipeline log.
- Branches, commit messages and PRs follow CONTRIBUTING.md. No `Co-Authored-By` or "Generated with" lines.
- You may commit and open PRs (`gh pr create`) once a human has pushed the branch. Review fixes are new commits unless the human asks for an amend.
- Everything in the repo is in English: plain words, short sentences.
- A behaviour change updates its docs in the same commit (docs/DESIGN.md, ADRs, docs/REVIEW_GUIDE.md, docs/postman_collection.json).
