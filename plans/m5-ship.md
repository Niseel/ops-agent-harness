# Plan: M5 ship   (from [specs/ops-agent-harness.md](../specs/ops-agent-harness.md))

Status: draft (gate 1 before the milestone starts). Branch: `chore/m5-ship`. PR title: `chore: M5 ship`.

Make the project easy to run and to review: one Docker command, full CI, end-to-end scenario evals, final docs and a verified review guide.

## Files that change
- `Dockerfile`, `.dockerignore` (new), `docker-compose.yaml` (edit) - one image serving API and UI; `app` profile
- `backend/app/main.py` (edit) - serve the built UI when it exists
- `.github/workflows/ci.yml` (edit) - frontend job, Qdrant service container for search tests
- `evals/run.sh` (new), `evals/check.sh` (edit), `evals/*.json` (new) - seven scenarios and the grader
- `README.md`, `docs/DESIGN.md`, `docs/postman_collection.json` (edit) - final run and test instructions, contract checked against the real API
- `docs/adr/0005-kb-search-hybrid-rag.md` (edit) - add measured results to its Validation section
- `docs/REVIEW_GUIDE.md` (edit) - real `file:symbol`, test names and manual steps
- `CLAUDE.md` (edit) - eval commands

## Order of work
1. T1 and T2 in parallel, then T3, then T4, then T5.

## Tasks
| ID | Task | Files | Skills | Depends on | Parallel | Est. |
|----|------|-------|--------|------------|----------|------|
| T1 | Dockerfile and compose `app` profile; FastAPI serves the built UI | Dockerfile, docker-compose.yaml, backend/app/main.py | - | M4 | yes | 0.5h |
| T2 | CI: frontend job, Qdrant service container, `live`-marked tests skipped | .github/workflows/ci.yml | - | M4 | yes | 0.5h |
| T3 | Scenario evals in the spec format: approve incident, retry, malformed repair, step limit (`limits`), injection rejected, `sparse_only` (`embeddings` fault, `expect.search_mode`), timeout after commit; `run.sh` and `check.sh` (AC-18) | evals/ | ai-engineer | T1, T2 | no | 1h |
| T4 | Final README, DESIGN and Postman against the real API; eval numbers in ADR 0005 (AC-17) | README.md, docs/ | secure-api-review | T3 | no | 1h |
| T5 | Review guide with real locations and test names, every row checked (AC-17) | docs/REVIEW_GUIDE.md | - | T4 | no | 0.5h |

## Risks
- The image grows with RAGAS dependencies; keep dev dependencies out of it.
- Scenario evals depend on `FakePlanner` rules and fixtures; a change there must update the scenario files in the same commit.
- Postman is checked with `npx newman run docs/postman_collection.json` against a running API; the collection must not depend on ids typed by hand.

## Proof
| AC | Evidence |
|----|----------|
| AC-17 | Clean clone follows README (fake LLM): tests pass, demo run with approval completes; `npx newman run docs/postman_collection.json` passes; each review-guide row checked |
| AC-18 | `evals/run.sh` output: every scenario passes `evals/check.sh` |

## Pipeline log
| Phase | Result | Notes |
|-------|--------|-------|
