"""Runs, approvals, resume and cancel (spec: API). The rules live in the runner; routes map them to HTTP."""

from functools import partial
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, ValidationError, model_validator

from app.api import get_runner
from app.auth import current_user, require_approver
from app.config import Strict
from app.harness.runner import NotFound, Runner
from app.harness.tool_gateway import describe

router = APIRouter(prefix="/api", tags=["runs"])
RunnerDep = Annotated[Runner, Depends(get_runner)]
APPROVAL_STATUSES = Literal["pending", "approved", "rejected", "edited", "expired", "cancelled"]


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
