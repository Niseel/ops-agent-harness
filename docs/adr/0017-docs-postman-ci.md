# 0017. Docs in repo, Postman collection, CI

Status: Accepted · Date: 2026-09-26

## Context

Reviewers must understand the design, run the project, call the API and check the tests quickly. Docs that live outside the repo drift from the code.

## Options

| Option | Pros | Cons |
|---|---|---|
| Markdown in the repo | Versioned with the code. Readable on GitHub. | Less visual than slides. |
| Markdown + slide deck | Good for a presentation. | Extra work; two sources to keep in sync. |
| Slides only | Visual. | Not versioned with the code. |

## Decision

Markdown in the repo, in English, short and plain:

| File | Content |
|---|---|
| `README.md` | What it is, how to run and test. |
| `docs/DESIGN.md` | Approach, architecture, database design, stack, environment variables, limitations, future improvements. |
| `docs/adr/` | These decision records. |
| `docs/REVIEW_GUIDE.md` | Each requirement of the brief mapped to code, tests, demo steps and the ADR behind it. |
| `docs/PLAN.md` | Tasks, estimates and milestones. |
| `docs/postman_collection.json` | Every endpoint, in the order a reviewer would call them. Written from the API contract first, then checked against the running API. |

GitHub Actions runs backend tests (with a Qdrant service container) and frontend tests on every push.

## Consequences

- A behaviour change must update its docs in the same commit.
- The review guide is checked at the end by running every "how to verify" step.
