# 0017. Docs in repo, Postman collection, CI

Status: Accepted · Date: 2026-09-26 · `docs/PLAN.md` replaced by `plans/`, see [0018](0018-adopt-ai-sdlc-workflow.md) · Refined in M5: pytest uses in-memory Qdrant ([0006](0006-vector-store-qdrant-no-rerank.md)); the Qdrant service container serves the end-to-end job

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
