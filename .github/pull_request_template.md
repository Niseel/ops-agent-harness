## What

<!-- One or two sentences: what this milestone or fix delivers. -->

## Plan

`plans/<file>.md` - tasks: T1, T2, ...

## Acceptance criteria

| AC | Test or evidence | Result |
|----|------------------|--------|
| AC-? | `backend/tests/<file>.py::<test>` | pass |

## Checks

- [ ] `cd backend && uv run pytest -q` passes
- [ ] `cd backend && uv run ruff check . && uv run ruff format --check .` passes
- [ ] Docs updated where behaviour changed (DESIGN.md, ADRs, REVIEW_GUIDE.md, Postman collection)
- [ ] No test weakened, skipped or deleted; no secrets committed

<details>
<summary>Test output</summary>

```
paste here
```

</details>

## Review

<!-- Reviewer subagent verdict per task, and any leftover nits. -->
