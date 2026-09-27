# Plan: M0 foundation   (from [specs/ops-agent-harness.md](../specs/ops-agent-harness.md))

Status: approved. Branch: `chore/m0-foundation`. PR title: `chore: M0 foundation`.

Everything a reviewer or a coding agent needs before any harness code: project skeleton, decisions, workflow, spec, plans, design report, API contract and review guide.

## Files that change
- `backend/`, `config.yaml`, `docker-compose.yaml` (new) - skeleton: FastAPI health, config split, JSON logs, Qdrant
- `docs/adr/` (new) - decisions 0000–0018
- `.claude/`, `.agents/`, `.github/`, `CLAUDE.md`, `AGENTS.md`, `CONTRIBUTING.md`, `REVIEW.md`, `docs/AI-SDLC.md`, templates (new) - workflow
- `intent/ops-agent-harness.md`, `specs/ops-agent-harness.md` (new) - why and what
- `plans/` (new) - roadmap and milestone plans
- `docs/DESIGN.md`, `.env.example` (new) - design report and environment variables
- `docs/postman_collection.json` (new) - API contract as requests
- `docs/REVIEW_GUIDE.md` (new), `README.md` (edit) - requirement-to-evidence map

## Order of work
1. T1 → T2 → T3 → T4 → T5 (done in this order).
2. T6, T7 and T8 read the spec only; T8 links to T6 and T7, so it goes last.

## Tasks
| ID | Task | Files | Skills | Depends on | Parallel | Est. |
|----|------|-------|--------|------------|----------|------|
| T1 | Project skeleton: uv, config split, JSON logging, health, Qdrant compose | backend/, config.yaml, docker-compose.yaml | - | - | no | 1h |
| T2 | ADRs 0001–0017 | docs/adr/ | - | T1 | no | 1h |
| T3 | Adopt the AI-SDLC workflow, GitHub conventions, ruff, skills | .claude/, .github/, CLAUDE.md, CONTRIBUTING.md, ... | - | T2 | no | 1h |
| T4 | Intent and spec (R1–R19, AC-1–AC-18) | intent/, specs/ | ai-engineer | T3 | no | 1h |
| T5 | Roadmap and milestone plans | plans/ | - | T4 | no | 0.5h |
| T6 | Design report and environment variables (R13) | docs/DESIGN.md, .env.example | ai-engineer | T4 | yes | 1h |
| T7 | Postman collection, contract first (R14) | docs/postman_collection.json | secure-api-review | T4 | yes | 0.5h |
| T8 | Review guide skeleton keyed by R and AC IDs (R19) | docs/REVIEW_GUIDE.md, README.md | - | T5–T7 | no | 0.5h |

## Risks
- Docs drift from the ADRs. Every doc links to its ADR instead of copying decisions, and the reviewer checks spec, ADRs and config together.
- The Postman collection is written before the API exists. M5 T4 runs it against the real API and fixes it.

## Proof
- AC-17 (partly): `docs/DESIGN.md` covers approach, database design, stack, environment variables, limitations and future work; `plans/README.md` lists tasks, estimates and milestones; every decision links to an ADR.
- Mechanical checks per commit: `.github/scripts/check-conventions.sh`, `uv run ruff check .`, `uv run pytest -q`.
- The review guide rows are completed and verified in M5 T5.

## Pipeline log
| Phase | Result | Notes |
|-------|--------|-------|
| T1 commit | 977c7a9 | 4 tests pass |
| T2 commit | e226ddf | 17 ADRs |
| T3 commit | ef24618 | kit trimmed and English only; hooks verified (push blocked) |
| T4 review | REQUEST_CHANGES → fixed | round 1: golden set path, `max_repairs` boundary, cancel vs pending approval; round 2: ADR 0003 note |
| T4 commit | 0e79f04 | ADR status notes on 0003, 0008, 0010, 0011 |
| T5 review | REQUEST_CHANGES → fixed | round 1: M1 task order (retry, faults), incident without approval in M1, owner of the resume endpoint, missing proofs, per-task estimates, scenario format |
| T5 review | APPROVE | round 2; minors fixed: AC-9 label in M1, `embeddings` fault counter and owner, injected-document ranking risk |
| T5 commit | 109708c | plans for M0–M5 |
| T6 review | REQUEST_CHANGES → fixed | API table rule now points at the spec; future items; fault-injection wording; `DATA_DIR`/`CONFIG_PATH`; relative paths from repo root (bug found while writing `.env.example`). Owner created `.env.example` (agents are denied `.env.*`) |
