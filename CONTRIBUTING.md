# Contributing

How changes move through this repo. The full loop is in [docs/AI-SDLC.md](docs/AI-SDLC.md).

## Who does what

| Step | Agent (Claude Code) | Owner (human) |
|---|---|---|
| Plan | Writes `plans/mN-*.md` | Approves the plan (gate 1) |
| Build | Writes code, tests and docs for one task; runs the tester and reviewer subagents | Approves the commit (gate 2) |
| Commit | Commits on the milestone branch | |
| Push | Never (blocked by a hook) | Pushes the branch |
| Pull request | Opens it with `gh pr create` after the push | Reviews and merges |

## Branches

`<type>/<slug>`, lower case, one branch per milestone or fix. Never commit to `main` directly.

```
chore/m0-foundation
feat/m1-harness-core
fix/approval-double-decision
```

Types are the same as for commits.

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <summary>

<body: what changed and why, wrapped at 72 characters>

Plan: plans/m1-harness-core.md T3
```

- **type**: `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `build`, `ci`, `chore`, `revert`
- **scope** (optional): `harness`, `llm`, `tools`, `kb`, `eval`, `api`, `cli`, `frontend`, `adr`
- **summary**: imperative, starts lower case, no final period, whole subject line at most 100 characters
- One plan task = one commit. Reference the task in a `Plan:` line.
- No `Co-Authored-By` or "Generated with" lines.
- Review fixes are new commits. Amend only when the owner asks, and only before the branch is pushed.

## Pull requests

- One pull request per milestone. Title in commit format, for example `feat: M1 harness core`.
- Fill in [the template](.github/pull_request_template.md): plan, acceptance criteria with their tests, check output, reviewer verdict.
- Merge with **Rebase and merge** so every reviewed commit stays in `main`.

## Checks

Run the commands in [CLAUDE.md](CLAUDE.md) before every commit. CI runs them again on every pull request, together with branch, title and commit message checks (`.github/scripts/check-conventions.sh`).
