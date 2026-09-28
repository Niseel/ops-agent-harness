"""Health, tools and incidents (spec: API)."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.api import get_runner
from app.config import cfg, settings
from app.eval import metrics
from app.harness.runner import Runner
from app.kb import qdrant
from app.tools.registry import TOOLS

router = APIRouter(prefix="/api", tags=["meta"])
RunnerDep = Annotated[Runner, Depends(get_runner)]
_INCIDENT_FIELDS = ("id", "run_id", "title", "description", "severity", "status", "created_at")


async def _probe(call: Callable[[], Awaitable], within_s: float = 2.0) -> Any:
    """The call's result, or None when it raises or takes longer than `within_s`."""
    try:
        async with asyncio.timeout(within_s):
            return await call()
    except Exception:
        return None


@router.get("/health")
async def health(runner: RunnerDep) -> dict:
    """Always 200. `degraded` only when the database does not answer. No URLs, keys or error texts."""
    try:
        kb = qdrant.get_kb()
    except Exception:
        kb = None  # every knowledge-base probe below then fails
    judge = metrics.get_judge()
    db, llm, embeddings, qdrant_ok, judge_ok, mode = await asyncio.gather(
        _probe(runner.store.ping),
        _probe(runner.llm_reachable),
        _probe(lambda: kb.embedder.embed(["ping"]), cfg.kb.embed_timeout_s),
        _probe(lambda: kb.client.get_collections()),
        _probe(judge.reachable),
        _probe(lambda: kb.status()),
    )
    mode = mode or "unavailable"
    if mode == "hybrid" and embeddings is None:
        mode = "sparse_only"  # the index has dense vectors, but queries cannot be embedded now
    return {
        "status": "ok" if db else "degraded",
        "llm_default": settings.llm_default,
        "db": {"ok": bool(db)},
        "llm": {"model": settings.llm_model, "reachable": bool(llm)},
        "embeddings": {"model": kb.embedder.model if kb else settings.embed_model, "reachable": embeddings is not None},
        "qdrant": {"reachable": qdrant_ok is not None},
        "judge": {"model": judge.model, "reachable": bool(judge_ok)},
        "kb": {"mode": mode},
    }


@router.get("/tools")
async def list_tools() -> list[dict]:
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_model.model_json_schema(),
            "requires_approval": tool.requires_approval,
        }
        for tool in TOOLS.values()
    ]


@router.get("/incidents")
async def list_incidents(runner: RunnerDep) -> list[dict]:
    # The spec's fields only: the idempotency key is internal.
    return [{key: incident[key] for key in _INCIDENT_FIELDS} for incident in await runner.store.list_incidents()]
