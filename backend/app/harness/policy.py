"""Run options and the checks every proposed tool call passes before it runs (spec: Limits; ADR 0011, 0012).

Pure functions: no I/O and no events. The agent node (T6) calls `check_calls`
and the runner calls `parse_options`; bad options raise ValueError (422 in the API).
"""

import json
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, create_model

from app.config import Limits, cfg
from app.harness.state import err
from app.harness.tool_gateway import check_input
from app.tools.faults import Faults
from app.tools.registry import TOOLS


class _StrictIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)  # strict: `true` or "5" is not a limit


# Every key of config.yaml > limits, optional, at least 1.
LimitsIn = create_model(
    "LimitsIn",
    __base__=_StrictIn,
    **{name: (field.annotation | None, Field(None, ge=1)) for name, field in Limits.model_fields.items()},
)


class OptionsIn(_StrictIn):
    limits: LimitsIn | None = None
    faults: Faults | None = None
    evaluate: bool | None = None


class RunOptions(BaseModel):
    """Effective options, stored in runs.options_json."""

    limits: Limits
    faults: Faults = Faults()
    evaluate: bool | None = None  # None until M2 resolves the default


def parse_options(raw: dict | None, *, allow_faults: bool) -> RunOptions:
    given = OptionsIn.model_validate({} if raw is None else raw)
    faults = given.faults or Faults()
    if not allow_faults and faults.model_dump(exclude_none=True):
        raise ValueError("fault injection is off (ALLOW_FAULT_INJECTION=false)")
    asked = given.limits.model_dump(exclude_none=True) if given.limits else {}
    # A client may lower a limit, never raise it above config.yaml. Values are already checked by LimitsIn.
    limits = cfg.limits.model_copy(
        update={name: min(value, getattr(cfg.limits, name)) for name, value in asked.items()}
    )
    return RunOptions(limits=limits, faults=faults, evaluate=given.evaluate)


def recursion_limit(limits: Limits) -> int:
    # Backstop only: one step is at most guard, agent, approval and tools; plus finalize and slack.
    return 4 * limits.max_steps + 5


def call_key(call: dict) -> str:
    return f"{call['name']}:{json.dumps(call['args'], sort_keys=True)}"


@dataclass
class Checked:
    pending: list[dict]  # {id, name, args, refusal}; refusal is an error envelope or None
    call_counts: dict[str, int]  # a new dict; the input is not changed
    error: str | None  # "max_tool_calls" when a call was blocked by that limit


def check_calls(
    calls: list[dict], *, limits: Limits, tool_calls: int, call_counts: dict[str, int], incidents: int
) -> Checked:
    """Check each call in reply order; the first failing check wins. Calls allowed earlier in the reply count."""
    counts, allowed, incidents_allowed, error = dict(call_counts), 0, 0, None
    pending = []
    for call in calls:
        tool = TOOLS.get(call["name"])
        if tool is None:
            refusal = err("validation", f"unknown tool {call['name']!r}")
        else:
            refusal = check_input(tool, call["args"])[1]
        if refusal is None:
            key = call_key(call)
            if tool_calls + allowed >= limits.max_tool_calls:
                refusal = err("blocked", f"tool call limit reached ({limits.max_tool_calls} per run)")
                error = "max_tool_calls"
            elif counts.get(key, 0) >= limits.max_repeat_calls:
                refusal = err("blocked", f"same call with the same arguments already allowed {counts[key]} times")
            elif call["name"] == "create_incident" and incidents + incidents_allowed >= limits.max_incidents_per_run:
                refusal = err("blocked", f"incident limit reached ({limits.max_incidents_per_run} per run)")
        if refusal is None:
            allowed += 1
            counts[key] = counts.get(key, 0) + 1
            incidents_allowed += call["name"] == "create_incident"
        pending.append({**call, "refusal": refusal})
    return Checked(pending, counts, error)
