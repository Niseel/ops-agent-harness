"""Golden-set evaluation over SSE and the latest report (spec: API, Evaluation)."""

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import Field

from app.api import get_runner
from app.auth import current_user
from app.config import Strict, cfg
from app.eval import golden, metrics
from app.harness.runner import Runner
from app.kb import qdrant

router = APIRouter(prefix="/api", tags=["eval"])
RunnerDep = Annotated[Runner, Depends(get_runner)]
eval_log = logging.getLogger("app.eval")


class EvalRequest(Strict):
    modes: list[Literal["hybrid", "dense", "sparse"]] = Field(min_length=1)


async def available_kb() -> qdrant.KnowledgeBase:
    """503 before streaming (and before the audit event) when the knowledge base cannot answer."""
    try:
        kb = qdrant.get_kb()
        if await kb.status() != "unavailable":
            return kb
    except Exception:
        pass
    raise HTTPException(503, "knowledge base unavailable")


@router.post("/eval/kb", response_class=EventSourceResponse)
async def eval_kb(
    kb: Annotated[qdrant.KnowledgeBase, Depends(available_kb)],
    runner: RunnerDep,
    body: EvalRequest | None = None,
) -> AsyncIterator[ServerSentEvent]:
    """`progress` events with {done, total}, then `report`, or `error`. A client that leaves stops the job."""
    modes = tuple(dict.fromkeys(body.modes)) if body else golden.MODES  # duplicates dropped, order kept
    await runner.audit(None, current_user(), "start_eval", cfg.eval.golden_set)
    updates: asyncio.Queue = asyncio.Queue()

    async def progress(done: int, total: int) -> None:
        updates.put_nowait({"done": done, "total": total})

    def finished(task: asyncio.Task) -> None:
        updates.put_nowait(None)
        # Logged here, so a failure is logged even when the client has already left.
        if not task.cancelled() and (exc := task.exception()) is not None:
            eval_log.error("golden-set evaluation failed", exc_info=exc)

    job = asyncio.create_task(golden.run_golden(kb, metrics.get_judge(), runner.store, modes=modes, progress=progress))
    job.add_done_callback(finished)
    try:
        while (update := await updates.get()) is not None:
            yield ServerSentEvent(event="progress", data=update)
        if job.exception() is not None:
            yield ServerSentEvent(event="error", data={"detail": "evaluation failed"})
            return
        # The stream skips MaskedJSONResponse, so mask here as the tracer does for events.
        yield ServerSentEvent(event="report", data=runner.tracer.mask(job.result()))
    finally:
        job.cancel()  # no-op when finished; else the report is never stored


@router.get("/eval/kb/latest")
async def latest_report(runner: RunnerDep) -> dict:
    report = await runner.store.latest_eval_report()
    if report is None:
        raise HTTPException(404, "no evaluation report yet")
    return report
