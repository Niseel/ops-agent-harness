"""One tool call, start to end (spec: Tools, Tool results and errors; ADR 0010).

`execute` checks the call, runs it with a timeout per attempt, retries
`timeout` and `unavailable`, checks the output, truncates long results and
emits one `tool` event per attempt. It always returns an envelope; the caller
(the tools node) turns it into a tool message and updates the counters.
"""

import asyncio
import json
import logging
import time

from pydantic import ValidationError

from app.config import cfg
from app.harness.retry import backoff
from app.harness.state import RETRYABLE, err, ok
from app.harness.store import Store
from app.harness.tracer import Tracer
from app.tools import Tool, ToolContext, ToolError
from app.tools.faults import ToolFault, hits
from app.tools.registry import TOOLS

log = logging.getLogger("app.tools")

_APPROVED = ("approve", "edit")


def describe(exc: ValidationError) -> str:
    """Pydantic errors as short `field: problem` text, without the rejected values.

    Field names are cut to 50 characters: an unknown field's name comes from the LLM.
    """
    return "; ".join(f"{'.'.join(str(part)[:50] for part in e['loc']) or 'input'}: {e['msg']}" for e in exc.errors())


def check_input(tool: Tool, args: object) -> tuple[object | None, dict | None]:
    """(validated input, None), or (None, validation envelope)."""
    try:
        return tool.input_model.model_validate(args), None
    except ValidationError as exc:
        return None, err("validation", f"invalid arguments for {tool.name}: {describe(exc)}")


async def execute(
    call: dict,
    *,
    run_id: str,
    decision: object,
    attempts_before: int,
    fault: ToolFault | None,
    store: Store,
    tracer: Tracer,
) -> tuple[dict, int]:
    """Run one call `{id, name, args}`. Returns (envelope, attempts made); 0 attempts = refused before running.

    `decision` is the operator's decision for this call (M3): `{decision: approve | edit, args}`.
    An `edit` decision's args replace the call's args here, so the tool always runs what the
    person approved. `attempts_before` is the run's `tool_attempts[name]`, which the fault counts from.
    """
    name = call["name"]
    tool = TOOLS.get(name)
    verdict = decision.get("decision") if isinstance(decision, dict) else None
    if tool is None:
        envelope = err("validation", f"unknown tool {name!r}")
    elif tool.requires_approval and verdict not in _APPROVED:
        envelope = err("blocked", f"{name} needs a person's approval and none was given")
    else:
        if verdict == "edit":
            call = {**call, "args": decision.get("args")}  # no args: fails validation, never runs the original
        args, envelope = check_input(tool, call["args"])
    if envelope is not None:
        await _tool_event(tracer, run_id, call, 0, 0.0, envelope, last=True)
        return envelope, 0

    ctx = ToolContext(run_id=run_id, tool_call_id=call["id"], store=store, tracer=tracer)
    tool_cfg = cfg.tool(name)
    for attempt in range(1, tool_cfg.max_attempts + 1):
        faulted = fault if hits(fault, attempts_before + attempt - 1) else None
        started = time.perf_counter()
        envelope = await _attempt(tool, args, ctx, faulted, tool_cfg.timeout_s)
        retry = not envelope["ok"] and envelope["error"]["type"] in RETRYABLE and attempt < tool_cfg.max_attempts
        await _tool_event(tracer, run_id, call, attempt, started, envelope, last=not retry)
        if not retry:
            return envelope, attempt
        delay = backoff(attempt, cfg.retry.base_delay_s, cfg.retry.max_delay_s)
        reason = envelope["error"]["type"]
        await tracer.emit(
            run_id,
            "retry",
            node="tools",
            tool=name,
            status="retry",
            attention="warn",
            msg=f"{name} attempt {attempt} failed ({reason}); retrying in {delay:.2f} s",
            data={"tool_call_id": call["id"], "attempt": attempt, "delay_s": round(delay, 3), "reason": reason},
        )
        await asyncio.sleep(delay)

    raise AssertionError("unreachable: the last attempt always returns")


async def _attempt(tool: Tool, args: object, ctx: ToolContext, fault: ToolFault | None, timeout_s: float) -> dict:
    try:
        async with asyncio.timeout(timeout_s):
            raw = await _run(tool, args, ctx, fault, timeout_s)
    except TimeoutError:
        return err("timeout", f"no result within {timeout_s} s")
    except ToolError as exc:
        return err(exc.type, exc.message)
    except Exception as exc:  # a crash in the tool counts as the tool being down; CancelledError is not caught
        log.warning("tool %s raised", tool.name, exc_info=True, extra={"run_id": ctx.run_id, "tool": tool.name})
        return err("unavailable", f"{tool.name} failed ({type(exc).__name__})")

    try:
        data = tool.output_model.model_validate(raw).model_dump(mode="json")
    except ValidationError as exc:
        return err("bad_output", f"{tool.name} returned data that breaks its output model: {describe(exc)}")
    text = json.dumps(data)
    limit = cfg.output.max_tool_result_chars
    if len(text) > limit:
        return {**ok(text[:limit]), "truncated": True}
    return ok(data)


async def _run(tool: Tool, args: object, ctx: ToolContext, fault: ToolFault | None, timeout_s: float) -> object:
    """The tool's raw output, with the fault applied (ADR 0012). Timeouts wait for the real timeout."""
    mode = fault.mode if fault else None
    if mode == "timeout":
        await asyncio.sleep(timeout_s + 1)
    if mode == "error":
        raise ToolError("unavailable", f"{tool.name} is unavailable (injected fault)")
    if mode == "bad_output":
        return {"fault": "bad_output"}  # the tool does not run, so nothing is committed
    if mode == "latency":
        await asyncio.sleep(fault.ms / 1000)
    raw = await tool.run(args, ctx)
    if mode == "timeout_after_commit":
        await asyncio.sleep(timeout_s + 1)
    return raw


async def _tool_event(
    tracer: Tracer, run_id: str, call: dict, attempt: int, started: float, envelope: dict, *, last: bool
) -> None:
    name = call["name"]
    status = "ok" if envelope["ok"] else envelope["error"]["type"]
    attention = _attention(name, envelope) if last else None  # a retried failure: its retry event is the warning
    detail = "" if envelope["ok"] else f" ({envelope['error']['message']})"
    await tracer.emit(
        run_id,
        "tool",
        node="tools",
        tool=name,
        status=status,
        attention=attention,
        msg=f"{name} attempt {attempt}: {status}{detail}",
        data={
            "tool_call_id": call["id"],
            "attempt": attempt,
            "args": call["args"],
            "duration_ms": round((time.perf_counter() - started) * 1000, 1) if attempt else 0,
            "result": envelope,
        },
    )


def _attention(name: str, envelope: dict) -> str | None:
    if envelope["ok"]:
        return "success" if name == "create_incident" else None
    return "warn" if envelope["error"]["type"] in ("validation", "bad_output") else "error"
