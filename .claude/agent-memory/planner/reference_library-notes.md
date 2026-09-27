---
name: library-notes-pointer
description: Where the researched LangGraph / checkpoint-sqlite / openai API facts live, and which ones later milestones (resume, M2 tools) depend on
metadata:
  type: reference
---

Library research (checked 2026-09-27 on PyPI and official docs) is written in plans/m1-harness-core.md, section "Library notes": langgraph 1.2.12, langgraph-checkpoint-sqlite 3.1.1, aiosqlite 0.22.1, openai 3.19.2 (the SDK now uses httpx2, same as the repo's dev dependency).

Facts M2/M3 planning will need again:
- Runtime `context=` is not checkpointed: pass it on every `ainvoke`, resumes with `Command(resume=...)` included.
- `ainvoke(..., durability="sync", version="v2")` returns `GraphOutput(.value, .interrupts)`.
- Checkpoint deserialisation is locked down with `JsonPlusSerializer(allowed_msgpack_modules=None)`.
- openai `AsyncOpenAI(max_retries=0)`, or SDK retries hide attempts from the trace.

Useful sources: reference.langchain.com (API signatures), docs.langchain.com/oss/python/langgraph/interrupts, pypi.org/pypi/<pkg>/<ver>/json (dependency ranges), raw.githubusercontent.com source for openai types and the sqlite saver.

**How to apply:** before relying on these in a later plan, re-check the version on PyPI; if it moved a minor version, re-read the changelog at docs.langchain.com/oss/python/releases/changelog.
