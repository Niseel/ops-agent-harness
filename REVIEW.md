# Review instructions

## Passes
Run three passes and tag each finding with its pass:
- Bugs: logic errors, broken edge cases, subtle regressions.
- Security: injection, missing input validation, secrets in logs or errors, tool output used as code. For API changes apply .claude/skills/secure-api-review.
- Compliance: the change matches specs/*.md, plans/*.md, docs/adr/ and CONTRIBUTING.md.

## Harness invariants (check on every change)
- Nothing with a side effect runs before `interrupt()`. `create_incident` runs only after an approved or edited decision.
- Every tool result that reaches the LLM is an envelope: `ok` with `data`, or `error` with `type`, `message`, `retryable`.
- Only retryable errors (`timeout`, `unavailable`) are retried, and every attempt emits an event.
- Client limits are clamped; nothing raises them above config.yaml.
- Every acceptance criterion in the plan has a test. No test was weakened, skipped or deleted.
- Docs changed with behaviour (docs/DESIGN.md, ADRs, docs/REVIEW_GUIDE.md, docs/postman_collection.json).

## What "Important" means
Important = BLOCKER or MAJOR in the reviewer's output. Reserve it for findings that break behaviour, leak data, or breach policy. Style and naming are nits.

## Cap the nits
Report at most five nits per review; summarize the rest as a count.

## Do not report
Generated files (uv.lock, package-lock.json) and anything CI already enforces (formatting, lint, commit and branch format).
