# Ops Agent Harness

**Reviewing this project? Start with [docs/REVIEW_GUIDE.md](docs/REVIEW_GUIDE.md).**

An agent harness for an operations assistant: an LLM–tool loop with validated tools, persisted state, retries and limits, human approval before side effects, evaluation, and a web UI that shows every step. No API key is needed: a fake LLM that follows fixed rules is the default.

## Run with Docker

```bash
docker compose --profile app up --build
```

Then open http://localhost:8000: the UI and the API on one port. The first build takes a few minutes. This runs the fake LLM and BM25-only search, and keeps the state in a Docker volume. Stop it with `docker compose --profile app down`. Stop a host API on :8000 first.

## Run on the host

Prerequisites: [uv](https://docs.astral.sh/uv/), Docker (for Qdrant), Node.js 22.22+, 24.15+ or 26+ (UI).

```bash
docker compose up -d qdrant
cd backend && uv sync --locked && uv run uvicorn app.main:app --port 8000 --timeout-graceful-shutdown 5
```

Leave the API running; no `--reload`, since a restart marks running runs `interrupted`. In a second terminal, the UI on http://localhost:4200 (it calls the API on :8000 through the dev proxy):

```bash
cd frontend && npm ci && npm start
```

Or build the UI once (`cd frontend && npm ci && npm run build`) before starting the API, and the API serves it at http://localhost:8000 (an API started before the build needs a restart).

## Try it

- **UI**: start a run from the form, watch the timeline, and approve the incident in the inbox. The fault switches and limits start the demo scenarios in [docs/REVIEW_GUIDE.md §4](docs/REVIEW_GUIDE.md#4-demo-scenarios).
- **CLI** (from `backend/`): `uv run python -m app.cli run --no-eval "payments-api is returning 5xx errors. Investigate and open an incident if needed."`, then answer the approval prompt with `a`. Without `--no-eval` the command waits for online evaluation after the run, which takes minutes with a slow local judge.
- **Postman** (from the repo root): `npx --yes newman@6.2.2 run docs/postman_collection.json --folder "1. Demo flow: create → approve → trace"` creates a run, approves the incident and checks the trace.

## Test

With the API running as above for the scenario evals:

```bash
(cd backend && uv run pytest -q)                   # backend tests
(cd frontend && npm test -- --watch=false)         # frontend tests
(cd frontend && npm run build && npx prettier --check src)
evals/run.sh                                       # seven scenario evals against the API
(cd backend && uv run python -m app.cli eval)      # golden-set search evaluation
```

- `live` tests run only when an embedding or judge endpoint answers. With a reasoning model as judge the judge test fails ([DESIGN §8](docs/DESIGN.md#8-limitations)); `-m "not live"` skips them.
- `evals/run.sh` needs fault injection (`ALLOW_FAULT_INJECTION=true`, the default) and `jq`.
- `cli eval` asks the judge for every row: about 1.5 h with a local reasoning judge, and its scores stay empty (DESIGN §8). `JUDGE_BASE_URL=http://127.0.0.1:9/v1 uv run python -m app.cli eval` turns the judge off and gives the retrieval metrics in seconds, as in [ADR 0005](docs/adr/0005-kb-search-hybrid-rag.md#validation).

## Use a real model

The defaults point at LM Studio on `http://localhost:1234/v1`. When it runs, the host API uses its embeddings (hybrid search) and its judge (online evaluation); runs keep the fake LLM until a run picks `openai` or `LLM_DEFAULT=openai` is set. For another OpenAI-compatible endpoint, copy `.env.example` to `.env` and set `LLM_*`, `EMBED_*` and `JUDGE_*` (all variables: [DESIGN §7](docs/DESIGN.md#7-configuration)). From Docker, `localhost` is the container: see the Docker note in [DESIGN §8](docs/DESIGN.md#8-limitations).

## Documents

- [docs/DESIGN.md](docs/DESIGN.md): approach, stack, database, safety, configuration, limitations.
- [docs/adr/](docs/adr/README.md): architecture decisions.
- [specs/ops-agent-harness.md](specs/ops-agent-harness.md): requirements and acceptance criteria.
- [plans/README.md](plans/README.md): milestones, tasks and estimates.
- [docs/AI-SDLC.md](docs/AI-SDLC.md) and [CONTRIBUTING.md](CONTRIBUTING.md): how the project is built.
