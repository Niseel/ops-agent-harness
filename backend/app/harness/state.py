"""Run state, statuses and the tool result envelope (spec: Agent loop, Tool results and errors)."""

import operator
from enum import StrEnum
from typing import Annotated, Any, Literal, TypedDict


class RunStatus(StrEnum):
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    LIMIT_EXCEEDED = "limit_exceeded"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


FINAL_STATUSES = frozenset(
    {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.LIMIT_EXCEEDED, RunStatus.TIMED_OUT, RunStatus.CANCELLED}
)

ErrorType = Literal["validation", "not_found", "timeout", "unavailable", "bad_output", "rejected", "blocked"]
RETRYABLE = frozenset({"timeout", "unavailable"})


def ok(data: Any) -> dict:
    return {"ok": True, "data": data}


def err(error_type: ErrorType, message: str) -> dict:
    return {"ok": False, "error": {"type": error_type, "message": message, "retryable": error_type in RETRYABLE}}


class AgentState(TypedDict):
    """What LangGraph checkpoints after every step. `messages` appends; every other field is replaced."""

    run_id: str
    objective: str
    messages: Annotated[list[dict], operator.add]
    pending: list[dict]  # calls of the current reply: {id, name, args, refusal}
    decisions: dict[str, Any]  # tool_call_id -> value returned by interrupt()
    steps: int
    tool_calls: int
    tool_attempts: dict[str, int]
    llm_attempts: int
    embed_attempts: int
    call_counts: dict[str, int]
    repairs: int
    incidents: int
    status: str
    final: str | None
    error: str | None
