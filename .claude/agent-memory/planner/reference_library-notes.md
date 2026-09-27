---
name: library-notes-pointer
description: Where the researched library facts live (LangGraph, checkpoint-sqlite, openai in the M1 plan; qdrant-client, ragas, instructor in the M2 plan) and which ones later milestones depend on
metadata:
  type: reference
---

Library research (checked 2026-09-27 on PyPI, upstream source at release tags, and official docs) lives in each plan's "Library notes" section:
- plans/m1-harness-core.md: langgraph 1.2.12, langgraph-checkpoint-sqlite 3.1.1, aiosqlite 0.22.1, openai 3.19.2 (the SDK uses httpx2, same as the repo's dev dependency).
- plans/m2-kb-and-eval.md: qdrant-client 1.19.1, ragas 0.4.3, instructor 1.17.0, the langchain-community pin.

Facts M3/M5 planning will need again:
- Runtime `context=` is not checkpointed: pass it on every `ainvoke`, resumes with `Command(resume=...)` included.
- `ainvoke(..., durability="sync", version="v2")` returns `GraphOutput(.value, .interrupts)`.
- Checkpoint deserialisation is locked down with `JsonPlusSerializer(allowed_msgpack_modules=None)`.
- openai `AsyncOpenAI(max_retries=0)`, or SDK retries hide attempts from the trace. Embeddings need `encoding_format="float"` for non-OpenAI servers.
- qdrant-client in-memory mode applies `Modifier.IDF` like the server, so tests and CI need no Qdrant container.
- ragas 0.4.3 hard-imports a module that langchain-community 0.4.2 removed: keep `langchain-community>=0.4.1,<0.4.2` until ragas fixes it (issues #2745, #2995). ragas analytics are on unless `RAGAS_DO_NOT_TRACK=true`.

Useful sources: reference.langchain.com (API signatures), docs.langchain.com/oss/python/langgraph/interrupts, pypi.org/pypi/<pkg>/<ver>/json (dependency ranges), raw.githubusercontent.com/<org>/<repo>/<tag>/... for exact source at a release, github.com/<org>/<repo>/tree/<tag>/... to see whether a file exists in a release.

**How to apply:** before relying on these in a later plan, re-check the version on PyPI; if it moved a minor version, re-read the changelog, and re-check open issues for the ragas pin.
