# Ops Agent Harness

**Reviewing this project? Start with [docs/REVIEW_GUIDE.md](docs/REVIEW_GUIDE.md).**

An agent harness for an operations assistant: LLM–tool loop, validated tools, persisted state, retries and limits, human approval before side effects, and a UI that shows every step.

Work in progress. Design docs, run instructions and the review guide land in `docs/`.

How we work: [docs/AI-SDLC.md](docs/AI-SDLC.md) (intent → spec → plan → test → review) and [CONTRIBUTING.md](CONTRIBUTING.md) (branches, commits, pull requests). Decisions: [docs/adr/](docs/adr/).

## Run (skeleton)

Prerequisites: [uv](https://docs.astral.sh/uv/), Docker, Node.js 22.22+, 24.15+ or 26+ (UI).

```bash
docker compose up -d qdrant
cd backend && uv sync && uv run uvicorn app.main:app --reload --port 8000 --timeout-graceful-shutdown 5
```

In a second terminal, the UI on http://localhost:4200 (it calls the API on :8000 through the dev proxy):

```bash
cd frontend && npm ci && npm start
```

Tests:

```bash
cd backend && uv run pytest
cd frontend && npm test -- --watch=false
```
