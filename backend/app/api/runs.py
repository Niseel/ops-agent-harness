"""Runs, approvals, resume, cancel and live events (spec: API). The rules live in the runner; routes map them
to HTTP."""

import asyncio
from collections.abc import AsyncIterator
from functools import partial
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import Field, ValidationError, model_validator

from app.api import get_runner
from app.auth import current_user, require_approver
from app.config import Strict
from app.harness.runner import NotFound, Runner
from app.harness.state import FINAL_STATUSES
from app.harness.tool_gateway import describe

router = APIRouter(prefix="/api", tags=["runs"])
RunnerDep = Annotated[Runner, Depends(get_runner)]
APPROVAL_STATUSES = Literal["pending", "approved", "rejected", "edited", "expired", "cancelled"]
IDLE_S = 15.0  # a quiet stream checks the run row this often (FastAPI's keep-alive interval)
GRACE_S = 5.0  # how long a stream waits for `done` once the run row is final


class CreateRun(Strict):
    objective: str  # 1–2000 characters, checked in create_run with the options
    llm: Literal["fake", "openai"] | None = None
    options: dict | None = None


class Decision(Strict):
    decision: Literal["approve", "reject", "edit"]
    reason: str | None = Field(None, max_length=500)
    args: dict | None = None

    @model_validator(mode="after")
    def _fits_the_decision(self) -> "Decision":
        if self.decision == "reject" and not (self.reason or "").strip():
            raise ValueError("reject needs a reason")
        if self.decision == "edit" and self.args is None:
            raise ValueError("edit needs args")
        if self.decision != "edit" and self.args is not None:
            raise ValueError("args are only for edit")
        return self


def unprocessable(exc: ValueError) -> HTTPException:
    detail = describe(exc) if isinstance(exc, ValidationError) else str(exc)
    return HTTPException(422, detail)


@router.post("/runs", status_code=202)
async def create_run(body: CreateRun, runner: RunnerDep) -> dict:
    try:
        run = await runner.create_run(body.objective, llm=body.llm, options=body.options)
    except ValueError as exc:
        raise unprocessable(exc) from None
    await runner.audit(run["id"], current_user(), "create_run", run["id"])
    runner.start(run["id"])
    return {"run_id": run["id"], "status": "running"}


@router.get("/runs")
async def list_runs(runner: RunnerDep, limit: Annotated[int, Query(ge=1, le=100)] = 20) -> list[dict]:
    return await runner.store.list_runs(limit)


@router.get("/runs/{run_id}")
async def get_run(run_id: str, runner: RunnerDep) -> dict:
    detail = await runner.run_detail(run_id)
    if detail is None:
        raise NotFound(f"run {run_id} not found")
    return detail


@router.get("/runs/{run_id}/trace")
async def get_trace(run_id: str, runner: RunnerDep) -> dict:
    run = await runner.store.get_run(run_id)
    if run is None:
        raise NotFound(f"run {run_id} not found")
    return {"run_id": run_id, "status": run["status"], "events": await runner.store.list_events(run_id)}


async def existing_run(run_id: str, runner: RunnerDep) -> dict:
    run = await runner.store.get_run(run_id)
    if run is None:
        raise NotFound(f"run {run_id} not found")
    return run


@router.get("/runs/{run_id}/events", response_class=EventSourceResponse)
async def run_events(
    run: Annotated[dict, Depends(existing_run)],
    runner: RunnerDep,
    last_event_id: Annotated[int | None, Header(ge=0, le=2**63 - 1)] = None,  # SQLite's integer range
) -> AsyncIterator[ServerSentEvent]:
    """Stored events after Last-Event-ID, then live ones, up to `done`. Nothing after `done`."""
    run_id, sent = run["id"], last_event_id or 0
    # Subscribe before the first read, so no event falls between the replay and the live part.
    wake = runner.tracer.subscribe(run_id)
    try:
        done = await runner.store.done_seq(run_id)
        if done is not None and done <= sent:
            return  # the client already has `done`
        # The store is the source: a live event only wakes the loop. So an event stored but never published
        # (a segment cancelled between the two) is sent too. ponytail: one query per wake-up.
        loop = asyncio.get_running_loop()
        idle, last, deadline = True, False, None
        while True:
            for event in await runner.store.list_events(run_id, after_seq=sent):
                yield ServerSentEvent(data=event, id=str(event["seq"]))
                sent = event["seq"]
                if event["kind"] == "done":
                    return
            if last:
                return  # the run row is final and no `done` came: a crash between the two writes
            if deadline is None and idle and (await runner.store.get_run(run_id))["status"] in FINAL_STATUSES:
                # `done` follows the final row, but not at once: a cancel first stops the segment and writes an
                # audit event. Wait for it up to GRACE_S.
                deadline = loop.time() + GRACE_S
            try:
                async with asyncio.timeout_at(deadline if deadline is not None else loop.time() + IDLE_S):
                    await wake.get()
                idle = False
            except TimeoutError:
                idle = True
                last = deadline is not None  # read once more, then end
            while not wake.empty():
                wake.get_nowait()
    finally:
        runner.tracer.unsubscribe(run_id, wake)


@router.get("/approvals")
async def list_approvals(runner: RunnerDep, status: Annotated[APPROVAL_STATUSES | None, Query()] = None) -> list[dict]:
    return await runner.store.list_approvals(status=status)


@router.post("/runs/{run_id}/approvals/{approval_id}", dependencies=[Depends(require_approver)])
async def decide(run_id: str, approval_id: str, body: Decision, runner: RunnerDep) -> dict:
    try:
        approval = await runner.decide(
            run_id,
            approval_id,
            decision=body.decision,
            reason=body.reason,
            args=body.args,
            actor=current_user(),
        )
    except ValueError as exc:  # invalid edited args: the approval stays pending
        raise unprocessable(exc) from None
    runner.spawn(run_id, partial(runner.continue_run, run_id))
    return approval


@router.post("/runs/{run_id}/resume", status_code=202)
async def resume(run_id: str, runner: RunnerDep) -> dict:
    result = await runner.request_resume(run_id, actor=current_user())
    runner.spawn(run_id, partial(runner.continue_run, run_id))
    return result


@router.post("/runs/{run_id}/cancel")
async def cancel(run_id: str, runner: RunnerDep) -> dict:
    return await runner.cancel(run_id, actor=current_user())
