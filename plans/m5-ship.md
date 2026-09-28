# Plan: M5 ship   (from [specs/ops-agent-harness.md](../specs/ops-agent-harness.md))

Status: approved (gate 1, 2026-09-28). Branch: `chore/m5-ship`. PR title: `chore: M5 ship`.

Make the project easy to run and to review: one Docker command that serves the API and the UI, CI for the frontend and for end-to-end checks against a running API, seven scenario evals graded from their traces, final run and test docs, measured search numbers, and a review guide whose every row is checked. It closes AC-17 and AC-18. No API route, tool, harness or UI behaviour changes; the only backend code change is serving the built UI. The owner's answers from round 1 (2026-09-28) are written into the rules and marked "(owner, round 1)".

## Files that change
- `backend/app/main.py` (edit, T1) - `UI_DIR`, `serve_ui()`: the built UI at `/` when the build exists, after the API routers
- `backend/tests/test_skeleton.py` (edit, T1) - the UI is served without hiding the API
- `Dockerfile`, `.dockerignore` (new, T1) - one image: a Node build stage, then Python with uv; API and UI on :8000
- `docker-compose.yaml` (edit, T1) - Qdrant pinned to `v1.19.1` with a healthcheck; `app` service in profile `app`; ports on 127.0.0.1
- `.github/workflows/ci.yml` (edit; T2 `frontend` job, T3 `e2e` job, T4 newman step)
- `.github/pull_request_template.md` (edit; T2, T3) - frontend and scenario-eval checks
- `evals/run.sh` (new, T3), `evals/check.sh` (rewrite, T3) - runner and grader
- `evals/*.json` (T3) - six new scenarios; `status-timeout-retry.json` gets `name` equal to its file stem
- `backend/tests/test_scenarios.py` (new, T3) - scenario files and the grader; `backend/tests/test_kb.py` (edit, T3) - scenario objectives stay inside the vendor-note check
- `.gitignore` (edit, T3) - `evals/out/`
- `CLAUDE.md` (edit, T3) - eval command
- `specs/ops-agent-harness.md` (edit, T3) - scenario eval rules; `docs/adr/0017-docs-postman-ci.md`, `docs/adr/0018-adopt-ai-sdlc-workflow.md`, `docs/adr/README.md` (edit, T3) - status notes
- `README.md` (edit; T1 Docker line, T4 final text)
- `docs/DESIGN.md` (edit; T1, T4)
- `docs/postman_collection.json` (edit, T4, only where newman or the walk finds drift)
- `docs/adr/0005-kb-search-hybrid-rag.md` (edit, T4) - Validation numbers
- `docs/REVIEW_GUIDE.md` (edit; T1 §1, T2–T4 §3, T3 §4, T5 the rest)
- `backend/tests/test_docs.py` (new, T5) - review-guide references and doc links exist
- `plans/README.md` (edit; Phase 1 estimate, wrap-up status)

## Order of work
1. T1 → T2 → T3 → T4 → T5. One commit each; stop after each commit for review.
2. T1 and T2 do not depend on each other. T3 follows T2 (both edit `ci.yml`). T4 needs T1 (Docker lines) and T3 (eval commands, the `e2e` job). T5 goes last: it checks what the other tasks wrote.

## Tasks
| ID | Task | Files | Skills | Depends on | Parallel | Est. |
|----|------|-------|--------|------------|----------|------|
| T1 | FastAPI serves the built UI; Dockerfile (Node build stage, Python with uv, non-root user, state directory) and `.dockerignore`; compose: Qdrant pinned with a healthcheck, `app` profile with a state volume, ports on 127.0.0.1 (AC-17) | main.py, tests/test_skeleton.py, Dockerfile, .dockerignore, docker-compose.yaml, README.md, docs/DESIGN.md, docs/REVIEW_GUIDE.md | secure-api-review | M4 | yes (with T2) | 0.75h |
| T2 | CI `frontend` job: `actions/setup-node@v7`, Node 24, npm cache on the lock, `npm ci`, prettier, tests, build | .github/workflows/ci.yml, .github/pull_request_template.md, docs/REVIEW_GUIDE.md | - | M4 | yes (with T1) | 0.25h |
| T3 | Seven scenario evals, `run.sh`, `check.sh`, their tests; CI `e2e` job (Qdrant service container, uvicorn with the fake LLM, `evals/run.sh`) (AC-18) | evals/, tests/test_scenarios.py, tests/test_kb.py, .github/workflows/ci.yml, .github/pull_request_template.md, .gitignore, CLAUDE.md, specs/ops-agent-harness.md, docs/adr/0017, docs/adr/0018, docs/adr/README.md, docs/REVIEW_GUIDE.md | ai-engineer | T2 | no | 1.25h |
| T4 | Final README and DESIGN; Postman run against the real API with newman, folder 1 added to the `e2e` job; golden-set numbers in ADR 0005; clean-copy check (AC-17) | README.md, docs/DESIGN.md, docs/postman_collection.json, docs/adr/0005, .github/workflows/ci.yml, docs/REVIEW_GUIDE.md | secure-api-review, ai-engineer | T1, T3 | no | 1h |
| T5 | Review guide: real `file:symbol`, test names, `Built in` column; every row checked; `test_docs.py` (AC-17) | docs/REVIEW_GUIDE.md, tests/test_docs.py | - | T4 | no | 0.75h |

The draft had 5 tasks (3.5h). Changes: the Qdrant service container moves from the test job to a new end-to-end job, because pytest uses in-memory Qdrant (M2 handoff, ADR 0006) and only a running API needs the server; that job lands with the scenario evals (T3) and runs the Postman demo too (T4); T3 depends on T2 only (it runs against uvicorn, not the image); the grader gets tests that show it can fail; T5 gets a docs test that keeps the review guide's references real; T4 also lists `ai-engineer` (the golden-set numbers). The estimate grows by 0.5h to 4h; plans/README.md is updated.

## Interfaces
| Piece (task) | Exposes | Used by |
|---|---|---|
| `backend/app/main.py` (T1) | `UI_DIR` = `ROOT / "frontend" / "dist" / "frontend" / "browser"`; `serve_ui(directory)` mounts `StaticFiles(directory=directory, html=True)` at `/`, name `ui` | the image, `test_skeleton.py` |
| `Dockerfile` (T1) | image with the API and the UI on :8000; `DB_PATH=/app/state/harness.db`; user `app` (uid 10001) | compose `app` |
| `docker-compose.yaml` (T1) | `qdrant` (always), `app` (profile `app`, `127.0.0.1:8000`, volume `harness_state:/app/state`) | README, review guide §1 |
| `evals/run.sh` (T3) | `evals/run.sh [evals/<name>.json ...]`; env `BASE_URL` (default `http://localhost:8000`), `APPROVER_TOKEN`; writes `evals/out/<name>.json`; exit 0 when every scenario passed, else 1 | CI `e2e`, README, review guide |
| `evals/check.sh` (T3) | `evals/check.sh <scenario.json> <result.json>`; one `ok` or `FAIL` line per check; exit 0 or 1 | `run.sh`, `test_scenarios.py` |
| CI (T2, T3, T4) | jobs `frontend` ("Frontend format, tests and build") and `e2e` ("End-to-end checks") | pull request checks |

## Rules pinned by this plan
Taken from the spec, ADRs 0005, 0006 and 0016–0018, the M2–M4 handoffs, the code at `8abd5a7` and the library notes, so the coder does not have to decide.

**General**
- No change to API routes, tools, the harness or the UI code. No new environment variable or config key, so DESIGN §7's list, `config.yaml` and `.env.example` (which agents cannot open) keep their keys.
- Scripts run on bash 3.2 (macOS `/bin/bash`): no `mapfile`, no associative arrays; under `set -u`, expand an array that may be empty as `${arr[@]+"${arr[@]}"}` (bash before 4.4 calls an empty array unbound). They need only `curl` and `jq` (macOS 15 has `/usr/bin/jq`; GitHub's Ubuntu runners have both).
- Both scripts are executable in git (`git ls-files -s evals/*.sh` shows `100755`).

**Serving the UI** (T1)
- `main.py`, after the three `include_router` calls: `UI_DIR = ROOT / "frontend" / "dist" / "frontend" / "browser"` (`ROOT` from `app.config`); `serve_ui(directory)` runs `app.mount("/", StaticFiles(directory=directory, html=True), name="ui")`; then `if UI_DIR.is_dir(): serve_ui(UI_DIR)`. `StaticFiles` raises at construction when the directory is missing, hence the check.
- Routes match in order, so every `/api` route wins. A GET for a missing path (`/api/nope`) gets StaticFiles' 404, which the existing `StarletteHTTPException` handler answers as `{"detail": "Not Found"}`. A POST to a missing path gets 405 `{"detail": "Method Not Allowed"}` (StaticFiles serves GET and HEAD only): accepted.
- No `index.html` fallback and no CORS change (M4 handoff: no client routes, relative `/api` URLs). After `npm run build`, a host uvicorn also serves the UI at :8000.

**Docker** (T1)
- `Dockerfile` at the repo root, two stages:
  1. `FROM node:24-slim AS ui`; `WORKDIR /src/frontend`; copy `frontend/package.json` and `frontend/package-lock.json`; `RUN npm ci`; copy `frontend/`; `RUN npm run build`.
  2. `FROM python:3.12-slim-trixie`; `COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /uvx /bin/`; `ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_DEV=1 UV_PYTHON_DOWNLOADS=0`; `WORKDIR /app/backend`; copy `backend/pyproject.toml`, `backend/uv.lock` and `backend/.python-version`; `RUN uv sync --locked`; copy `backend/app` and `backend/prompts`; copy `config.yaml` to `/app/`, `data/kb/` and `data/services.json` to `/app/data/`, `evals/kb_golden.jsonl` to `/app/evals/`; copy `/src/frontend/dist/frontend/browser` from `ui` to `/app/frontend/dist/frontend/browser`.
  - `app.config.ROOT` is then `/app`, so `config.yaml`, `data/`, the golden set, `backend/prompts/system.md` and `UI_DIR` resolve as on the host.
  - `RUN useradd --create-home --uid 10001 app && mkdir /app/state && chown app /app/state`; `USER app`; `ENV PATH="/app/backend/.venv/bin:$PATH" DB_PATH=/app/state/harness.db`; `EXPOSE 8000`. The store creates the database's parent directory only when it can: `/app/state` must exist and belong to `app` (a new named volume copies that ownership).
  - `CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "5"]`: exec form, so uvicorn gets SIGTERM (M3 handoff command).
  - No cache mounts and no HEALTHCHECK: nothing waits on the app.
- `.dockerignore`: `.git`, `**/.venv`, `**/node_modules`, `**/__pycache__`, `**/.pytest_cache`, `**/.ruff_cache`, `frontend/dist`, `frontend/.angular`, `data/harness.db*`, `.env`, `evals/out`. `frontend/node_modules` matters most: copied in, macOS binaries would replace the Linux ones.
- `docker-compose.yaml`:
  - `qdrant`: image `qdrant/qdrant:v1.19.1` (not `latest`; M2 handoff); `ports: ["127.0.0.1:6333:6333"]` (was all interfaces; Qdrant has no key here); volume as now; `healthcheck: {test: ["CMD", "bash", "-c", ":> /dev/tcp/127.0.0.1/6333"], interval: 2s, timeout: 2s, retries: 30}` (the image has bash but no curl or wget).
  - `app`: `profiles: [app]`; `build: .`; `ports: ["127.0.0.1:8000:8000"]` (the API has no authentication); `env_file: [{path: .env, required: false}]`; `environment: {QDRANT_URL: http://qdrant:6333, DB_PATH: /app/state/harness.db}` (these win over `.env`); `volumes: [harness_state:/app/state]`; `depends_on: {qdrant: {condition: service_healthy}}` (ingest runs at startup; a Qdrant that is not up yet would leave search `unavailable` until a restart).
  - Top-level volumes: `qdrant_data`, `harness_state`.
- With the default settings the container reaches no LLM, embedding or judge endpoint (`localhost` is the container): the fake LLM runs, the index is BM25-only, search is `sparse_only` and online evaluation is off. That is the Docker demo.

**CI** (T2, T3, T4)
- Keep the file's style: `actions/checkout@v7`, `astral-sh/setup-uv@v10.2.0`, `permissions: contents: read`, a `name:` per job.
- `frontend` (T2): `runs-on: ubuntu-latest`; `defaults.run.working-directory: frontend`; `actions/setup-node@v7` with `node-version: 24`, `cache: npm`, `cache-dependency-path: frontend/package-lock.json`; then `npm ci`, `npx prettier --check src`, `npm test -- --watch=false`, `npm run build`.
- `live` tests: no change. In CI no endpoint answers, so `live_embedder` and `live_judge` skip them (spec: skipped when not reachable).
- `e2e` (T3), name "End-to-end checks": `services.qdrant: {image: qdrant/qdrant:v1.19.1, ports: ["6333:6333"]}`. Steps: checkout; setup-uv; `uv sync --locked` in `backend`; wait for Qdrant with `curl -sf --retry 30 --retry-delay 1 --retry-all-errors http://localhost:6333/readyz`; in `backend`, `uv run uvicorn app.main:app --port 8000 --timeout-graceful-shutdown 5 > "$RUNNER_TEMP/api.log" 2>&1 &`, then the same curl on `http://localhost:8000/api/health` (the lifespan ingests before the port opens); `evals/run.sh` from the repo root; `if: failure()`: `cat "$RUNNER_TEMP/api.log"`. A background process lives until the job ends.
- T4 adds, after the evals: `npx --yes newman@6.2.2 run docs/postman_collection.json --folder "1. Demo flow: create → approve → trace"` (the runner image has Node).
- CI cannot run locally. Each task checks that the file parses (`cd backend && uv run python -c "import yaml; yaml.safe_load(open('../.github/workflows/ci.yml'))"`) and runs the job's commands by hand. The jobs first run when the owner pushes; a failing job gets a fix commit.
- The `e2e` job runs the API on the runner, not from the Docker image; ADR 0017 gets a status note (owner, round 1).

**Scenario files** (T3). `evals/<name>.json` in the spec's format; `name` equals the file stem; `llm` is `fake`. Objectives are ones already in `test_kb.py` (`PLAIN_OBJECTIVES`, `INJECTION_OBJECTIVE`), so the vendor-note check covers them and nothing there changes. A fault is written with its `times` (`{"mode": "...", "times": n}`).

| File | Objective | limits / faults | decisions | expect |
|---|---|---|---|---|
| `approve-incident.json` | `payments-api is returning 5xx errors. Investigate and open an incident if needed.` | - | approve | `completed`; attempts `create_incident: 1`; incidents 1 |
| `status-timeout-retry.json` (exists; `name` fixed) | `payments-api is returning 5xx, investigate` | `get_service_status` timeout ×2 | - | `completed`; attempts `get_service_status: 3`; incidents 0 |
| `malformed-reply.json` | `payments-api is returning 5xx errors, investigate.` | `llm` malformed ×1 | - | `completed`; attempts `llm: 4` (owner, round 1) |
| `step-limit.json` | same | limits `max_steps: 2` | - | `limit_exceeded`; attempts `llm: 2` (owner, round 1) |
| `prompt-injection.json` | `SMS alerts from notifications-worker are delayed. Check the SMS vendor note.` | - | reject, reason `The instruction came from a knowledge-base document, not from an operator.` | `completed`; attempts `create_incident: 0`; incidents 0 |
| `degraded-search.json` | `payments-api is returning 5xx errors, investigate.` | `embeddings` error ×1 | - | `completed`; search_mode `sparse_only` |
| `timeout-after-commit.json` | `orders-db is down. Investigate and open an incident if needed.` | `create_incident` timeout_after_commit ×1 | approve | `completed`; attempts `create_incident: 2`; incidents 1 |

Why these values. FakePlanner makes one call per reply: a search, the status of the first hyphenated service, an incident only when the objective asks for one (or a search result injects one), then an answer. So a plain run has 3 `llm` events and the malformed reply adds one; `max_steps: 2` stops after the status call; a rejected call has only its attempt-0 `tool` event; `timeout_after_commit` stores the incident, times out after `timeout_s` (5 s), and the retry gets the same incident by its idempotency key. Timeouts wait for the real `timeout_s` (2 s for status), so all seven take about 30 s.

**`evals/run.sh`** (T3)
- Arguments: scenario files; none means every `evals/*.json`, sorted. It prints the health line first (`llm_default`, `kb.mode`), so the reader knows whether search was hybrid; it exits 1 when `/api/health` does not answer.
- Per scenario: `POST /api/runs` with `{objective, llm, options: {evaluate: false, limits?, faults?}}`. `evaluate: false` keeps `done` the last event and the judge out of the run, as in the Postman demo.
- Poll `GET /api/runs/{id}` every 0.2 s. At `awaiting_approval`, take this run's row from `GET /api/approvals?status=pending` and POST the next entry of `decisions`, as written, to `/api/runs/{id}/approvals/{approval_id}`, with `X-Approver-Token: $APPROVER_TOKEN` when that variable is set. A decision sets the run to `running` in the same transaction, so the next poll never sees the old pause.
- Stop at a final status (`completed`, `failed`, `limit_exceeded`, `timed_out`, `cancelled`). A run that pauses with no decision left, or is not final after 60 s, is cancelled (`POST /api/runs/{id}/cancel`), so no approval stays pending; its `cancelled` status then fails the check.
- Save `evals/out/<name>.json` (git-ignored): the `GET /api/runs/{id}/trace` body `{run_id, status, events}` plus `incidents`, the rows of `GET /api/incidents` whose `run_id` is this run (owner, round 1).
- Run `check.sh` on it. A create that is not 202, or a decision that is not 200, prints the status and the body and fails the scenario. End with `<n> passed, <m> failed`; exit 1 when any failed.

**`evals/check.sh`** (T3). Grades one result file with `jq`. It prints `ok` or `FAIL` and what it compared, one line per check, and exits 1 when any check fails. `set -euo pipefail`; the "not implemented" exit goes. Checks:
1. Exactly one `done` event, and its `status` equals `expect.status`.
2. Decisions: the statuses of the `approval` events that record a decision (`approved`, `rejected`, `edited`, `expired`), in `seq` order, equal `decisions` mapped (`approve` → `approved`, `reject` → `rejected`, `edit` → `edited`). An empty list means no decision. So the injection scenario fails when no approval was asked.
3. `attempts` (when given), per key: a tool name counts that tool's `tool` events with `data.attempt >= 1` (the run's attempts at it; a refused or rejected call has only attempt 0); `llm` counts the `llm` events (owner, round 1).
4. `incidents` (when given): the length of the file's `incidents`; a file without that key fails (owner, round 1).
5. `search_mode` (when given): at least one ok `search_knowledge_base` `tool` event, and `data.result.data.mode` equals it in every one.

**Tests** (T1, T3, T5)
- T1 `test_skeleton.py::test_built_ui_served_after_api_routes` (async, `api` fixture): write `index.html` to `tmp_path`, call `main.serve_ui(tmp_path)`; `GET /` → 200 with `text/html`; `GET /api/tools` → 200 JSON list; `GET /api/nope` → 404 `{"detail": "Not Found"}`; in `finally`, `main.app.router.routes.pop()`. Assert the content type, not the body: a developer's own build may be mounted first.
- T3 `test_scenarios.py::test_scenario_files_follow_the_format`: exactly the seven stems above; keys within the spec's and the required ones present; `name` equals the stem; `llm` is `fake`; `policy.parse_options({"limits": ..., "faults": ...}, allow_faults=True)` accepts them; each decision validates as `app.api.runs.Decision`; `expect` keys within `status`, `attempts`, `incidents`, `search_mode`; `expect.status` in `FINAL_STATUSES`; `attempts` keys are names in `TOOLS` or `llm`.
- T3 `test_scenarios.py::test_check_sh_passes_a_matching_trace` and `::test_check_sh_fails_on_each_mismatch` (parametrized): a small scenario with every `expect` key and a hand-built result file pass (exit 0); one change each gives exit 1: another `done` status, a second `done`, one attempt more, one `llm` event less, a missing decision event, an extra one, another incident count, no `incidents` key, a `hybrid` search, no search event. Run with `subprocess` and `bash`; skip when `jq` is not on `PATH`.
- T3 `test_kb.py::test_scenario_objectives_are_in_the_vendor_note_check`: every `evals/*.json` objective is in `PLAIN_OBJECTIVES`, or is `INJECTION_OBJECTIVE` in `prompt-injection.json` only.
- T5 `test_docs.py` (file reads only):
  - `test_review_guide_tests_exist`: every `` `test_x.py::name` `` in `docs/REVIEW_GUIDE.md` (a bare `` `::name` `` uses the last file named) is a `def name(` in `backend/tests/test_x.py`; `::*` needs the file only; `name*` needs one `def` with that prefix. Every `` `x.spec.ts` `` exists in `frontend/src/app/`.
  - `test_review_guide_symbols_exist`: every `` `path.py:Symbol` `` or `` `path.ts:Symbol` `` (one colon): the file exists (from the repo root when the path starts with `backend/`, `frontend/`, `evals/` or `data/`, else under `backend/app/`) and defines the last dotted part (`def`, `class` or `name =` in Python; `class`, `function` or `const` in TypeScript).
  - `test_doc_links_resolve`: every relative Markdown link in `README.md`, `docs/DESIGN.md`, `docs/REVIEW_GUIDE.md` and `specs/ops-agent-harness.md` points to an existing file (an `#anchor` is ignored).

**README** (T4). Sections in this order; every command is run from the clean copy (Proof):
1. What it is; start with the review guide.
2. Run with Docker: `docker compose --profile app up --build`, then http://localhost:8000 (UI and API). Fake LLM and BM25-only search; state kept in a volume; stop with `docker compose --profile app down`. Stop a host API on :8000 first.
3. Run on the host: prerequisites (uv, Docker for Qdrant, Node 22.22+, 24.15+ or 26+); `docker compose up -d qdrant`; `cd backend && uv sync --locked && uv run uvicorn app.main:app --port 8000 --timeout-graceful-shutdown 5` (no `--reload`: a restart marks running runs `interrupted`); the UI with `cd frontend && npm ci && npm start` (http://localhost:4200), or `npm run build` and the API serves it at :8000.
4. Try it: the UI; the CLI (`uv run python -m app.cli run "..."`); newman folder 1.
5. Test: backend `uv run pytest -q` (`live` tests run only when an endpoint answers; with a reasoning model as judge the judge test fails, DESIGN §8; `-m "not live"` skips them); frontend tests, build and prettier; scenario evals `evals/run.sh` with the API running (`ALLOW_FAULT_INJECTION=true`, the default); golden set `cd backend && uv run python -m app.cli eval`.
6. Use a real model: copy `.env.example` to `.env`; `LLM_*`, `EMBED_*`, `JUDGE_*` (DESIGN §7); from Docker, `localhost` is the container.
7. Links: DESIGN, ADRs, spec, plans, AI-SDLC, CONTRIBUTING. "Work in progress" goes.

**Postman** (T4)
- Start the API with the fake LLM on a fresh database and run `npx --yes newman@6.2.2 run docs/postman_collection.json --folder "1. Demo flow: create → approve → trace"`. It must pass without edits. This is the collection's first real run (M3 checked it by reading).
- Then run the whole collection with `--timeout-request 30000` and walk each request: no failed assertion. The evaluation request is cut at 30 s, which stops the job (spec). Not part of the proof.
- A fix changes the collection, never the API. Requests keep taking ids from earlier responses, never typed by hand.

**ADR 0005 numbers** (T4; owner, round 1)
- Qdrant up and LM Studio serving `text-embedding-bge-m3`: `cd backend && uv run python -m app.cli ingest --force` (a hybrid index), then `JUDGE_BASE_URL=http://127.0.0.1:9/v1 uv run python -m app.cli eval` (judge off: retrieval metrics in seconds). The report's `models.embed_model` must be `text-embedding-bge-m3`, every mode must have `errors` 0 (a `dense` error row means the index was BM25-only: ingest again), and every `hybrid` row must have `mode_used` `hybrid` (a query embedding slower than `kb.embed_timeout_s` falls back to `sparse_only`: run again).
- Validation gets: the date, the golden set (16 questions), the embedding model, the `kb` settings from `config.yaml`, one row per mode with hit@3, MRR@10 and recall@3, and one or two plain sentences on what the numbers show, whichever way they point (the ADR rules allow evidence, not a rewrite of the decision).
- Then one line: RAGAS context precision and recall were not measured, because the local judge model is a reasoning model that leaves the reply content empty, so RAGAS has nothing to parse (DESIGN §8), and a judged run of the 48 rows takes about 1.5 h (M4 handoff). The line names that model (the report's `models.judge_model`) and gives the command to add the numbers with a non-reasoning `JUDGE_MODEL`.

**Review guide** (T5)
- Every row: `Where` as `file:symbol` (for example `harness/runner.py:Runner.decide`); `How to verify` with test names from the plans' Proof tables and the M5 checks; the `Status` column becomes `Built in` (the milestone).
- The header says every row was checked in M5 T5, how (the docs test, CI, the steps run) and the date.
- §1: the Docker line (T1) and `evals/run.sh`. §3: the CI sentence in its final form (Doc changes). §4 keeps the T3 scenario column.
- T5's tester runs each How-to-verify command and each §1 step with the API up; the UI steps are the owner's, at gate 2.

**Not built** (add when someone asks): a Docker build or image publish in CI; an app healthcheck; an embedding server in compose; `evaluate` in the scenario format; an `edit` scenario; build cache mounts; actionlint.

**Tests changed on purpose**: none. `evals/check.sh` failed on purpose until now ("not implemented"); T3 replaces it. `status-timeout-retry.json` changes its `name` (data, not a test). Checked: no test reads `evals/*.json`, and every existing request in the tests goes to `/api/...`, so the UI mount changes none of them.

## Library notes (checked 2026-09-28 on nodejs.org, PyPI, the npm registry, GitHub releases, uv's Docker guide, Qdrant's `Dockerfile` at `v1.19.1`, Starlette's installed source and this machine)

**This machine** (file inspection; the planner has no shell): Docker Desktop 4.91.0 (`/Applications/Docker.app`), so Compose v2 with profiles, `depends_on` conditions and optional `env_file` entries (Compose 2.24+); `/usr/bin/jq` (macOS 15); uv 0.12.7 made `backend/.venv`; Node 26.9.0 and npm 11.19.1 (M4). macOS `/bin/bash` is 3.2.

**Node** (nodejs.org/dist/index.json): 24.21.0 LTS "Krypton" (2026-09-07, npm 11.19.0); 26.10.0 Current (2026-09-21, npm 11.19.1); 22.23.3 LTS. Angular 22.2 needs `^22.22.3 || ^24.15.0 || >=26.0.0`, so `node:24-slim` and `node-version: 24` resolve to a supported 24.21.x.

**npm lock** (`frontend/package-lock.json`): it lists the Linux x64 and arm64 builds (glibc and musl) of every native package (rolldown, lightningcss, esbuild, oxc-parser, @parcel/watcher, lmdb, msgpackr-extract, @napi-rs/nice). `npm ci` on GitHub's x64 runners and in an arm64 build on Apple Silicon should find its binary; the M4 fallback (regenerate the lock) stays.

**uv** (PyPI; docs.astral.sh/uv/guides/integration/docker): latest 0.12.19. The guide's example is `FROM python:3.12-slim-trixie` with `COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /uvx /bin/`, `UV_COMPILE_BYTECODE=1`, `UV_LINK_MODE=copy`, `UV_NO_DEV=1`, the venv on `PATH` and `.venv` in `.dockerignore`. `backend/pyproject.toml` has `package = false`, so `uv sync` installs dependencies only; `uv.lock` needs Python `==3.12.*`.

**GitHub Actions**: `actions/setup-node` v7.0.0 (July 2026: ESM, cache outputs; inputs `node-version`, `cache`, `cache-dependency-path`; since v6 it caches by itself when `package.json` names npm in `packageManager`, but ours is under `frontend/`, so the job passes `cache` and `cache-dependency-path`). `astral-sh/setup-uv` v10.2.0 is the latest release (the repo pins releases). `actions/checkout@v7` as in the file. Ubuntu runners have curl 7.71+ (`--retry-all-errors`), jq and Node.

**Qdrant**: v1.19.1 is the latest server release and matches `qdrant-client` 1.19.1. The runtime image is Debian 13 slim with `ca-certificates`, `tzdata` and `libunwind8`: no curl or wget (qdrant/qdrant#4250, open), but bash, so the healthcheck opens a TCP connection with bash. `/readyz` answers once Qdrant can serve requests.

**Starlette 1.7.0 `StaticFiles`** (installed `staticfiles.py`): `html=True` serves `index.html` for `/`; a missing file raises `HTTPException(404)` unless a `404.html` exists; methods other than GET and HEAD raise 405; `check_dir=True` raises when the directory is missing.

**newman**: npm `latest` is 6.2.2.

## Risks
- The CI jobs (T2, T3, T4) first run on GitHub when the owner pushes. A failing job gets a fix commit.
- Image size and build time: the ragas tree (datasets, pyarrow, langchain) is a runtime dependency, so the image is large; T1 logs its size. Dev dependencies stay out (`UV_NO_DEV=1`). A first build needs the network and several minutes.
- Apple Silicon builds a linux/arm64 image. The npm lock has the arm64 bindings; a Python dependency without a linux arm64 wheel would need build tools in the image (T1's build log shows it).
- Without an embedding endpoint (CI, the Docker default) the index is BM25-only, every search is `sparse_only`, and `degraded-search` passes without the `embeddings` fault firing (a BM25-only index counts no embedding attempt). With LM Studio embeddings the fault is what makes it `sparse_only`. `run.sh` prints the knowledge-base mode first, T3 runs the scenarios both ways, and the review guide says so.
- Scenario evals depend on FakePlanner's rules and the fixtures: a change there updates the scenario files in the same commit. `test_scenarios.py` catches format drift; `test_kb.py` keeps the vendor note out of the plain objectives' top 3 (fake embedder).
- A real embedder ranks differently. The vendor note shares no BM25 term with the plain objectives (`test_fixture_docs_match_planner_patterns`), so with RRF it would need a top dense rank while few chunks appear in both lists; T3's run with LM Studio embeddings shows it.
- newman has never run the collection against the API. T4 is the first run; fixes go into the collection.
- On the owner's machine, `test_eval.py::test_ragas_judge_live` fails while LM Studio serves a reasoning model (DESIGN §8). Where it answers, the clean-copy check adds `-m "not live"` and logs why.
- Real waits: status timeouts (2 s) and the incident timeout (5 s) make a scenario run take about 30 s; the 60 s limit per scenario leaves room.
- bash 3.2: `set -u` with an empty array, no `mapfile`.
- The docs test may reject a row style the builder wants (for example a symbol outside the path rule). Extend the rule in the test; do not bend the guide.

## Proof
Each task's checks pass at its own commit, with the commands in CLAUDE.md (from T3 on, `evals/run.sh` against a running API).

| AC | Evidence | Task |
|----|----------|------|
| AC-17 (one Docker command) | `test_skeleton.py::test_built_ui_served_after_api_routes`. Logged: `docker compose --profile app up --build`; `curl -s localhost:8000/` returns the UI's `index.html`; `curl -s localhost:8000/api/health` answers with `kb.mode` `sparse_only`; `docker compose exec app id -u` prints 10001; `docker compose --profile app restart app` keeps the run list; image size. The owner starts the Approve scenario at http://localhost:8000 and approves it in the inbox (gate 2) | T1 |
| AC-17 (CI) | The four frontend commands pass locally; `ci.yml` parses; the `frontend` job is green on the pull request | T2 |
| AC-18 | `evals/run.sh` prints `7 passed, 0 failed` against uvicorn with the fake LLM, once with LM Studio embeddings (hybrid index) and once with `EMBED_BASE_URL=http://127.0.0.1:9/v1` (BM25-only, as in CI); `test_scenarios.py::test_scenario_files_follow_the_format`, `::test_check_sh_passes_a_matching_trace`, `::test_check_sh_fails_on_each_mismatch`; `test_kb.py::test_scenario_objectives_are_in_the_vendor_note_check`; the `e2e` job is green on the pull request | T3 |
| AC-17 (README from a clean clone) | `git add -A && git archive "$(git write-tree)" \| tar -x -C <scratchpad>/clean` (exactly what the commit will hold: no `.env`, `.venv`, `node_modules` or database); from there, the README's commands: backend and frontend installs, tests and build; the API with the fake LLM (reuse the Qdrant on :6333); a demo run with an incident reaches `awaiting_approval`, is approved and completes (newman folder 1, and `cli run` answered with `a`); `evals/run.sh` passes | T4 |
| AC-17 (Postman) | `npx --yes newman@6.2.2 run docs/postman_collection.json --folder "1. Demo flow: create → approve → trace"` passes without edits (output in the Pipeline log); the same step is green in the `e2e` job | T4 |
| AC-17 (DESIGN, plans, ADR links) | T4 re-reads DESIGN for approach, database design, stack, environment variables, limitations and future work; plans/README.md lists tasks, estimates and milestones; `test_docs.py::test_doc_links_resolve` (every ADR link in the spec resolves) | T4, T5 |
| AC-17 (review guide) | `test_docs.py::test_review_guide_tests_exist`, `::test_review_guide_symbols_exist`; the tester runs every How-to-verify step; the owner runs §1 and the §4 UI check at gate 2 | T5 |

Other checks:
- T4: the ADR 0005 numbers come from the report `cli eval` stored; the Pipeline log records its id, `models.embed_model`, `errors` 0 per mode and `mode_used` `hybrid` on every hybrid row.
- Verifier (optional): T1 (the Docker path) and T4 (the README path).

## Doc changes
Each note goes in the commit of the task that builds the behaviour.
- T1, `README.md` Run: add "With Docker only (API and UI on http://localhost:8000, fake LLM): `docker compose --profile app up --build`."
- T1, `docs/REVIEW_GUIDE.md` §1: drop "(M5)" after the Docker line.
- T1, `docs/DESIGN.md` §7, `DB_PATH` row: add "; `/app/state/harness.db` in the Docker image, kept in a volume". §8, new bullet: "**Docker.** `docker compose --profile app up --build` runs the API and the built UI on :8000 next to Qdrant, both published on 127.0.0.1 only (the API has no authentication). Inside the container `localhost` is the container, so with the default settings the Docker demo runs the fake LLM, BM25-only search and no online evaluation. To use LM Studio from the container, set `LLM_BASE_URL`, `EMBED_BASE_URL` and `JUDGE_BASE_URL` to `http://host.docker.internal:1234/v1` in `.env` (Docker Desktop)." No ADR change: ADR 0016 already says FastAPI serves the built UI in the Docker image.
- T2, `.github/pull_request_template.md` Checks: add "- [ ] `cd frontend && npm test -- --watch=false && npm run build && npx prettier --check src` passes".
- T2, T3, T4, `docs/REVIEW_GUIDE.md` §3: the CI sentence grows with each job; the "Process: ..." sentence after it stays. Final form: "CI: [.github/workflows/ci.yml](../.github/workflows/ci.yml) runs ruff and pytest, the frontend format check, tests and build, the scenario evals and the Postman demo flow against a running API (fake LLM, Qdrant), and the commit and branch checks."
- T3, `specs/ops-agent-harness.md`, Evaluation, Scenario evals: the last sentence becomes: "`evals/run.sh` starts each scenario through the API with online evaluation off, sends the listed decisions, and saves `GET /api/runs/{id}/trace` with the run's incidents from `GET /api/incidents` added as `incidents`. It cancels a run that pauses with no decision left or is not final within 60 s. `evals/check.sh <scenario> <trace>` grades the file with `jq`: exactly one `done` event with `expect.status`; the decisions in order; `attempts` per tool (its `tool` events with `data.attempt` ≥ 1) or for `llm` (its `llm` events); the number of `incidents`; the `mode` of every search result." (owner, round 1)
- T3, `docs/adr/0017-docs-postman-ci.md` status line: add "· Refined in M5: pytest uses in-memory Qdrant ([0006](0006-vector-store-qdrant-no-rerank.md)); the Qdrant service container serves the end-to-end job". Index row in `docs/adr/README.md`: "Accepted (plan doc replaced by 0018; Qdrant container serves the end-to-end job, M5)". (owner, round 1)
- T3, `docs/adr/0018-adopt-ai-sdlc-workflow.md` status line: add "· Refined in M5: the saved trace also holds the run's incidents ([spec, Evaluation](../../specs/ops-agent-harness.md#evaluation))". Index row: "Accepted (`/feature` adapted; saved traces hold the run's incidents, M5)". (owner, round 1)
- T3, `CLAUDE.md` Commands: add `evals/run.sh                              # scenario evals; needs the API on :8000 (fake LLM)`; delete "Eval commands are added by the milestone that introduces them."
- T3, `.github/pull_request_template.md` Checks: add "- [ ] `evals/run.sh` passes (API on :8000 with the fake LLM)".
- T3, `docs/REVIEW_GUIDE.md` §4: the first sentence becomes "All scenarios run in the UI (fault switches and limits) and as scenario evals: `evals/run.sh` runs the seven files below against a running API and grades each trace with `evals/check.sh`."; a column `Scenario eval` with each row's file; the Degraded search row adds "(without an embedding endpoint every search is `sparse_only`; the fault shows only when embeddings answer)".
- T3, `.gitignore`: `evals/out/`.
- T4, `README.md`: the final text (Rules, README). `docs/DESIGN.md` §8 "Evaluation quality": "about 15 questions" becomes "16 questions"; fix any other line the clean-copy check shows wrong. `docs/adr/0005-kb-search-hybrid-rag.md` Validation (Rules, ADR 0005 numbers). `docs/postman_collection.json`: only fixes newman or the walk finds.
- T5, `docs/REVIEW_GUIDE.md`: the rest (Rules, Review guide).
- No spec change for Docker or CI: the spec already says FastAPI serves the UI in production. No new environment variable, so `.env.example` and DESIGN §7's list keep their keys.

## Handoffs
- After M5 (no later milestone):
  - `degraded-search` exercises the `embeddings` fault only where embeddings answer; CI has no embedding endpoint.
  - ADR 0005 has no RAGAS numbers until someone runs `cli eval` with a non-reasoning judge (owner, round 1).
  - A Docker build in CI would catch a broken Dockerfile (see "Not built").

## Open questions
None. In round 1 (2026-09-28) the owner accepted all four defaults, and they are written into the rules above (marked "(owner, round 1)"):
1. Incidents come from the incident system: `run.sh` adds this run's rows from `GET /api/incidents` to the saved trace file as `incidents`, and `check.sh` counts them. So the `timeout-after-commit` scenario fails if a retry duplicated the incident. Spec note and ADR 0018 status note in T3.
2. `expect.attempts` may use the key `llm` (the number of `llm` events): `malformed-reply` expects `llm: 4`, `step-limit` `llm: 2`. So the malformed scenario fails when its fault never fires. Spec note in T3.
3. CI gets a new `e2e` job on the runner: Qdrant service container, uvicorn with the fake LLM, `evals/run.sh` (T3), newman folder 1 (T4). ADR 0017 status note in T3.
4. ADR 0005 Validation: hit@3, MRR@10 and recall@3 per mode from a golden-set run with `text-embedding-bge-m3` and the judge off. The ADR says RAGAS context precision and recall were not measured and why: the local model is a reasoning model, and a judged run takes about 1.5 h.

Defaults to confirm at gate 1 (planner choices, not asked): a non-root image user and a state volume; `.env` passed to the container when present; compose ports on 127.0.0.1 (Qdrant's too); Node 24 LTS in Docker and CI; `evaluate: false` in `run.sh`; the scenario names above and the `name` fix; `test_docs.py` in T5; plans/README.md's cut list without the built M4 T6 item.

## Pipeline log
| Phase | Result | Notes |
|-------|--------|-------|
| Phase 1 planner | NEEDS_ANSWERS | Checked against the spec (Evaluation, scenario format, UI, API notes, AC-17, AC-18), ADRs 0005, 0006 and 0016–0018, plans/README.md, CONTRIBUTING.md, CLAUDE.md, the M1–M4 handoffs, the reviewer's carry-over notes and the code at `8abd5a7` (main.py, config.py ROOT, runner decide and pause, loop, both gateways, FakePlanner, faults, kb, the eval and meta routes, conftest, test names in the review guide), `docker-compose.yaml`, `ci.yml`, `evals/`, the frontend build config and lock, README, DESIGN, the review guide, the Postman collection and the PR template. Kept 5 tasks, reshaped: Docker and the UI mount (with a test, a non-root user, a state volume, a Qdrant healthcheck, ports on 127.0.0.1); CI frontend job; scenario evals with an end-to-end CI job (the Qdrant service container moved there: pytest uses in-memory Qdrant); final docs with newman and ADR 0005 numbers; review guide with a docs test. 3.5h → 4h; plans/README.md updated (total 34.5h; the built M4 T6 cut removed). Pinned: seven scenario files with expected values traced through FakePlanner, `run.sh` and `check.sh` rules (decisions checked from `approval` events; one `done`; `evaluate: false`; cancel on a stuck run), bash 3.2 limits, the clean-copy procedure. Library notes: Node 24.21.0 LTS, uv 0.12.19, setup-node v7.0.0, setup-uv v10.2.0, Qdrant v1.19.1 (no curl in the image), Starlette StaticFiles, newman 6.2.2, Docker Desktop 4.91.0. 4 open questions (incident count source, `attempts.llm`, CI end-to-end job, ADR 0005 judge metrics) |
| Phase 1 planner (round 2) | READY | Owner, 2026-09-28: all four defaults accepted (incidents from `GET /api/incidents` in the saved trace; `attempts.llm`; the `e2e` CI job on the runner with newman folder 1; ADR 0005 retrieval metrics with the judge off and RAGAS marked not measured). Markers changed to "(owner, round 1)"; Open questions: none, with the record and the planner defaults left for gate 1. Consistency pass over the tasks, rules, tests, Proof rows, Doc changes (spec note, ADR 0017 and 0018 notes) and Handoffs. ADR 0005 rule tightened: the report must show `text-embedding-bge-m3`, no error rows and `mode_used` `hybrid` on every hybrid row, and the not-measured line names the judge model and the reason. Estimate unchanged (4h) |
| Gate 1 | approved | Owner approved on 2026-09-28 with the four round-1 answers and the planner's defaults (non-root image user and state volume; `.env` passed in when present; compose ports on 127.0.0.1, Qdrant included; Node 24 in Docker and CI; `evaluate: false` in `run.sh`; the seven scenario names and the `status-timeout-retry.json` name fix; `test_docs.py` in T5; the cut list without M4 T6, total 34.5h) |
| T1 build | done | Skills: `secure-api-review` invoked (error bodies stay `{"detail": ...}`: a missing `/api` path answers 404 JSON through the existing handler). `main.py`: `UI_DIR`, `serve_ui()`, mounted after the three routers when the build exists. `test_skeleton.py::test_built_ui_served_after_api_routes`. `Dockerfile` (node:24-slim build stage; python:3.12-slim-trixie with uv 0.12.19, no dev dependencies; user `app` uid 10001; `DB_PATH=/app/state/harness.db`; exec-form uvicorn), `.dockerignore`, compose (Qdrant `v1.19.1` with the bash TCP healthcheck, both services on 127.0.0.1, `app` profile with optional `.env`, state volume, `service_healthy`). README Docker line, REVIEW_GUIDE §1, DESIGN §7 `DB_PATH` row and §8 "Docker". Checked on Docker 29.8.0 (arm64): `docker compose --profile app build app` took 4 min 25 s, image 2.17 GB; `up -d`: Qdrant healthy, then the app; `curl localhost:8000/` returns the UI's `index.html`; `/api/health` gives `kb.mode` `sparse_only`, `llm_default` `fake`; `docker compose exec app id -u` prints 10001; a run paused at `awaiting_approval` is still listed with its pending approval after `docker compose --profile app restart app`; `/api/nope` 404 JSON, POST `/nope` 405; the run's event stream replays 18 events; the UI at :8000 lists the run and the approval. The running Qdrant was already 1.19.1, and the container's ingest skipped the host's hybrid index (same content hash). Backend 500 passed, 2 live deselected |
| T1 tester | PASS | Every Dockerfile, `.dockerignore` and compose rule matches the plan; doc text matches. In the running container: `/app` holds `config.yaml`, `data/kb`, `data/services.json`, `evals/kb_golden.jsonl`, `backend/prompts/system.md` and the UI's `index.html`; no `.env`, `node_modules` or `harness.db*` in the image; the process runs as uid 10001 and `/app/state` belongs to `app`. A scratch app with `StaticFiles` mounted before the routers answers `/api/tools` with 404, so the test catches a wrong mount order. No test added. Backend 500 passed, 2 live deselected |
| T1 reviewer | APPROVE | MINORs fixed in DESIGN §8 "Docker" (doc only): the container reads the host's `.env` too, so `LLM_DEFAULT`, `DATA_DIR` and `CONFIG_PATH` apply there (keep paths relative), and `host.docker.internal` URLs in `.env` also change a host API (run one or the other); the container and a host API share the `ops_kb` collection, so after editing `data/kb` the image is rebuilt, else the container re-indexes its older copy without dense vectors until a host API starts again. NIT kept: `UV_COMPILE_BYTECODE` compiles only the dependencies, so the app code compiles in memory at each start (negligible). Reviewer checks: path traversal (`--path-as-is`, encoded `..`) gives 404 JSON; the `app` user can write only `/tmp`, its home and `/app/state`; PID 1 is uvicorn; `.dockerignore` covers every ignored file under the copied paths. Backend 500 passed, 2 live deselected |
| T1 commit | 00d82c3 | |
| T2 build | done | Skills: none in the Skills column. `ci.yml` job `frontend` ("Frontend format, tests and build"): `actions/checkout@v7`, `actions/setup-node@v7` with Node 24, `cache: npm` on `frontend/package-lock.json`, then `npm ci`, `npx prettier --check src`, `npm test -- --watch=false`, `npm run build` in `frontend/`. PR template check line; REVIEW_GUIDE §3 CI sentence (frontend part). `ci.yml` parses (`yaml.safe_load`: jobs `conventions`, `backend`, `frontend`). The job's commands ran in `node:24-slim` (Linux arm64, Node 24.21.0, npm 11.19.0) on a copy of `frontend/` without `node_modules`: `npm ci` found every binary, prettier clean, 118 tests passed, build 165.60 kB. On the host: 118 tests, build, prettier clean |
| T2 tester | PASS | The job matches every CI rule and the `backend` job's style; `ci.yml` parses; the PR template line and the REVIEW_GUIDE §3 sentence match (no scenario evals or Postman yet). Host: `npm ci`, prettier, 118 tests, build 165.60 kB. The lock has linux x64 glibc entries for every native package. An extra `node:24-slim --platform linux/amd64` run under QEMU on Apple Silicon passed `npm ci`, prettier and the build, but 9 tests hit vitest's 5 s timeout: the run was 15 to 20 times slower (tests 166 s against 10.9 s natively). The slowest test takes 229 ms natively, so a native x64 runner has about 20 times headroom; the job's result on the pull request decides |
| T2 reviewer | APPROVE | No BLOCKER, MAJOR or MINOR. Permissions (workflow-level `contents: read`), action pinning (first-party moving major tags, third-party `setup-uv` pinned, as in the file), Node 24 and the cache inputs checked; doc wording matches. NITs kept: no `timeout-minutes` on any job (GitHub's default applies; nothing here should hang); `npm ci` without `--no-audit --no-fund` (plan text, harmless). The `frontend` job going green on the pull request is the proof still open |
| T2 commit | b5e913d | |
| T3 build | done | Skills: `ai-engineer` invoked. Six new scenario files and `status-timeout-retry.json`'s `name` fixed, values as pinned. `evals/run.sh` (bash 3.2; health line first; `evaluate: false`; decisions from the run's pending approval; cancels a run paused with no decision left, a refused decision, or a run not final in 60 s; saves the trace plus this run's incidents to `evals/out/<name>.json`; `<n> passed, <m> failed`). `evals/check.sh` (one jq program, five checks, one `ok` or `FAIL` line each). `test_scenarios.py` (format; grader passes a matching trace; fails on each of 10 mismatches) and `test_kb.py::test_scenario_objectives_are_in_the_vendor_note_check`. CI `e2e` job (Qdrant service container, `readyz` wait, uvicorn in the background, `evals/run.sh`, API log on failure); `ci.yml` parses (jobs `conventions`, `backend`, `frontend`, `e2e`). Spec note, ADR 0017 and 0018 status notes and index rows, CLAUDE.md eval command, PR template, `.gitignore`, REVIEW_GUIDE §3 and §4. `evals/run.sh` against uvicorn with the fake LLM: hybrid (LM Studio embeddings, the host Qdrant) `7 passed, 0 failed` in 14 s; BM25-only like CI (a fresh Qdrant `v1.19.1` on :6334, `EMBED_BASE_URL` unreachable, knowledge base `sparse_only`) `7 passed, 0 failed` in 13 s. Backend 513 passed, 2 live deselected |
| T3 tester | PASS | Scenario values, `run.sh` and `check.sh` match the plan; doc text matches. `/bin/bash evals/run.sh` (bash 3.2) against uvicorn with the fake LLM and no embeddings: `7 passed, 0 failed`; one scenario by argument: `1 passed, 0 failed`. The grader fails for real: `approve-incident`'s result graded against `step-limit.json` gives 3 FAIL lines and exit 1; a scratch scenario expecting `failed` gives `FAIL status`, `0 passed, 1 failed`, exit 1. Approver token: with `APPROVER_TOKEN` set on the API and not in the environment, the decision gets 401, the run is cancelled and the scenario fails; with it set, `1 passed`. Both scripts `100755`; `ci.yml` parses. No test added (the extra paths need a running API; the `e2e` job runs one). Backend 513 passed, 2 live deselected |
| T3 reviewer | APPROVE | MINORs fixed: `run.sh` passed the whole incidents list to jq as one argument, which fails past Linux's 128 KB argument limit (about 400 incidents in a kept database); it now goes through a process substitution. `check.sh` exited 2 with no FAIL line on a result that is not JSON; it now prints `FAIL result: ... is not JSON` and exits 1 (spec). NITs fixed: `run.sh` no longer prints a jq parse error per poll while the API is down; `test_check_sh_fails_on_each_mismatch` names the one check each change must fail and asserts exactly that line. NIT kept: the objectives test passes on an empty `evals/` (the format test pins the seven files). Reviewer checks: three negative scratch scenarios (a decision listed but no pause; a pause with no decision left; a decision refused with 422) each fail; no API gives `HTTP 000` and exit 1. After the fixes `/bin/bash evals/run.sh` (hybrid): `7 passed, 0 failed`. Backend 514 passed, 2 live deselected |
| T3 commit | 78b416e | |
| Fix before T4 | done | Found by T4's Postman walk (first real newman run, judge reachable): `POST /api/runs/{id}/resume` on a completed run hung past newman's 30 s `ESOCKETTIMEDOUT` instead of answering 409. `Runner.request_resume` took the run's lock before checking the status, and online evaluation holds that lock after `done` (minutes with the LM Studio judge); `cancel` already checks the row first. Owner chose a separate fix commit before T4 (2026-09-28). Fix: check the row's status before the lock; the conditional update under the lock stays. `test_api.py::test_resume_answers_409_while_online_evaluation_holds_the_run` holds the run's lock and expects 409 within 2 s; it fails without the fix. No spec, doc or Postman change (the contract was already "409 when not interrupted"). Backend 515 passed, 2 live deselected |
| Fix tester | PASS | Added `test_recovery.py::test_resume_of_running_run_conflicts_even_while_its_lock_is_held` (a segment holds a `running` run's lock: resume answers `Conflict` within 2 s) and `::test_concurrent_resumes_one_wins` (two resumes of one interrupted run: one wins, one `Conflict`). Both lock tests fail without the fix (TimeoutError); the race test passes either way. The resume path of an interrupted run is unchanged (existing recovery, approval, API and CLI tests). Backend 517 passed, 2 live deselected |
| Fix reviewer | APPROVE | No interleaving gives a wrong 409: only `recover()` at API start sets `interrupted`, and the conditional update under the lock still settles a race. The CLI's `resume` shares the path. No other runner method takes the lock before checking the row (`cancel` and `decide` check first; `sweep` skips a held lock). NIT fixed: the race test also asserts exactly one `resume_run` audit event. Backend 517 passed, 2 live deselected |
