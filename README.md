# Ops Agent Harness

An agent harness for an operations assistant: LLM–tool loop, validated tools, persisted state, retries and limits, human approval before side effects, and a UI that shows every step.

Work in progress. Design docs, run instructions and the review guide land in `docs/`.

How we work: [docs/AI-SDLC.md](docs/AI-SDLC.md) (intent → spec → plan → test → review) and [CONTRIBUTING.md](CONTRIBUTING.md) (branches, commits, pull requests). Decisions: [docs/adr/](docs/adr/).

## Run (skeleton)

Prerequisites: [uv](https://docs.astral.sh/uv/), Docker.

```bash
docker compose up -d qdrant
cd backend && uv sync && uv run uvicorn app.main:app --reload --port 8000
```

```bash
cd backend && uv run pytest
```
