---
name: library-notes-pointer
description: Where the researched library facts live (LangGraph, checkpoint-sqlite, openai in M1; qdrant-client, ragas, instructor in M2; FastAPI SSE, httpx2, uvicorn in M3; Angular 22.2, Vitest, Node on this machine in M4) and which ones later milestones depend on
metadata:
  type: reference
---

Library research (checked 2026-09-27 on PyPI, upstream source at release tags, the installed packages in backend/.venv, and official docs) lives in each plan's "Library notes" section:
- plans/m1-harness-core.md: langgraph 1.2.12, langgraph-checkpoint-sqlite 3.1.1, aiosqlite 0.22.1, openai 3.19.2 (the SDK uses httpx2, same as the repo's dev dependency).
- plans/m2-kb-and-eval.md: qdrant-client 1.19.1, ragas 0.4.3, instructor 1.17.0, the langchain-community pin.
- plans/m3-approval-api-cli.md: FastAPI 0.141.1 native SSE, Starlette 1.7.0, httpx2 2.13.1, uvicorn 0.54.0, LangGraph interrupt rules.
- plans/m4-ui.md (checked 2026-09-28): Angular 22.2.0 (TypeScript `>=6.0 <6.1` while npm `latest` TypeScript is 7.x; Vite 8.3; Vitest 5 with jsdom by default; zoneless and OnPush by default), FastAPI's SSE wire format, EventSource rules.
- plans/m5-ship.md (checked 2026-09-28): Node 24.21.0 LTS / 26.10.0 Current, uv 0.12.19 and its Docker guide, `actions/setup-node` v7.0.0, `astral-sh/setup-uv` v10.2.0, Qdrant server v1.19.1 (image has no curl), Starlette `StaticFiles`, newman 6.2.2, Docker Desktop 4.91.0 on this machine.

Facts M4/M5 planning will need again:
- Runtime `context=` is not checkpointed: pass it on every `ainvoke`, resumes with `Command(resume=...)` included.
- `ainvoke(..., durability="sync", version="v2")` returns `GraphOutput(.value, .interrupts)`; `aget_state(config).interrupts` holds pending interrupts; invoking with `None` after an `interrupt()` re-runs the node and pauses again; several `interrupt()` calls in one node are matched by index from a per-task list.
- FastAPI >= 0.135 has native SSE (`fastapi.sse.EventSourceResponse`, `ServerSentEvent`): generator endpoints, dependencies solved before streaming, `: ping` every 15 s via `fastapi.routing._PING_INTERVAL` (imported by name; tests patch it there).
- httpx2 `ASGITransport` and Starlette `TestClient` collect the whole response body: SSE tests must end the stream. `ASGITransport` does not run the lifespan.
- uvicorn waits forever for open connections at shutdown (`timeout_graceful_shutdown` default None): run with `--timeout-graceful-shutdown 5`.
- `EventSource` cannot POST: the UI reads `POST /api/eval/kb` with `fetch`.
- openai `AsyncOpenAI(max_retries=0)`, or SDK retries hide attempts from the trace. Embeddings need `encoding_format="float"` for non-OpenAI servers.
- qdrant-client in-memory mode applies `Modifier.IDF` like the server, so tests and CI need no Qdrant container.
- ragas 0.4.3 hard-imports a module that langchain-community 0.4.2 removed: keep `langchain-community>=0.4.1,<0.4.2` until ragas fixes it (issues #2745, #2995). ragas analytics are on unless `RAGAS_DO_NOT_TRACK=true`.

- This machine (2026-09-28): Homebrew Node 26.9.0 and npm 11.19.1 under `/opt/homebrew/Cellar/node/`. The planner has no shell: Glob does not follow symlinks (`/opt/homebrew/bin/node` looks missing); Grep on an explicit path does (use it to prove `/opt/homebrew/bin/npm` resolves).
- jsdom has no `EventSource`; tests stub it. `POST /api/eval/kb` with no body runs every mode; `{}` is a 422.

Useful sources: registry.npmjs.org/-/package/<name>/dist-tags and registry.npmjs.org/<name>/<version> (engines, peers); raw.githubusercontent.com/angular/angular-cli/v<version>/packages/schematics/angular/... (tags have a `v`; `utility/latest-versions/package.json` has what `ng new` pins); raw.githubusercontent.com/angular/angular/main/CHANGELOG.md (breaking changes); reference.langchain.com (API signatures), docs.langchain.com/oss/python/langgraph/interrupts, fastapi.tiangolo.com/tutorial/server-sent-events/, pypi.org/pypi/<pkg>/<ver>/json (dependency ranges), raw.githubusercontent.com/<org>/<repo>/<tag>/... for exact source at a release. The installed source under backend/.venv is readable with Read (Grep skips it because it is git-ignored).

**How to apply:** before relying on these in a later plan, re-check the version on PyPI or in uv.lock; if it moved a minor version, re-read the changelog, and re-check open issues for the ragas pin.
