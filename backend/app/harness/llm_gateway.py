"""One LLM turn for the agent node (spec: LLM, ADR 0010).

`next_reply` calls the model with the system prompt, the history and the tool
definitions; retries transient errors; classifies the reply; gives every tool
call an id that is unique in the run; and emits one `llm` event per attempt.
The caller (the agent node) decides what an outcome means for the run.
"""

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import openai

from app.config import cfg
from app.harness.retry import backoff
from app.harness.tracer import Tracer
from app.llm.openai_compat import LLMReply, ToolCall
from app.tools.faults import LLMFault, hits

PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "system.md"
SYSTEM_PROMPT = PROMPT_PATH.read_text(encoding="utf-8")
PROMPT_SHA = hashlib.sha256(PROMPT_PATH.read_bytes()).hexdigest()[:12]

_TRANSIENT_STATUS = {408, 409, 429}

Kind = Literal["final", "tool_calls", "malformed", "unavailable"]


@dataclass
class Outcome:
    kind: Kind
    llm_attempts: int  # the run's counter after this turn
    message: dict | None = None  # assistant message to append (final, tool_calls)
    calls: list[dict] = field(default_factory=list)  # {id, name, args}
    reason: str | None = None  # malformed or unavailable


def correction(reason: str) -> dict:
    return {"role": "user", "content": f"[harness] previous reply invalid: {reason}"}


def classify(reply: LLMReply, tool_names: set[str], max_calls: int) -> str | None:
    """The reason a reply is malformed, or None. Checked in the spec's order."""
    if reply.finish_reason == "length":
        return "reply was cut off (finish_reason=length)"
    if not (reply.content or "").strip() and not reply.tool_calls:
        return "empty reply: no text and no tool calls"
    if len(reply.tool_calls) > max_calls:
        return f"{len(reply.tool_calls)} tool calls in one reply; at most {max_calls} allowed"
    for call in reply.tool_calls:
        if call.name not in tool_names:
            return f"unknown tool {call.name!r}"
    for call in reply.tool_calls:
        try:
            json.loads(call.arguments)
        except (TypeError, ValueError):
            return f"arguments of {call.name} are not valid JSON"
    return None  # JSON that is not an object passes here and fails input validation later


def unique_ids(calls: tuple[ToolCall, ...], seen: set[str], step: int) -> list[str]:
    """Keep the provider's id unless it is missing or already used in the run or this reply."""
    ids, used = [], set(seen)
    for i, call in enumerate(calls):
        new = call.id if call.id and call.id not in used else f"s{step}c{i}"
        n = 1
        while new in used:  # a provider may already use the s<step>c<i> form
            n += 1
            new = f"s{step}c{i}-{n}"
        used.add(new)
        ids.append(new)
    return ids


def _transient(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError | openai.APIConnectionError):  # includes APITimeoutError
        return True
    if isinstance(exc, openai.APIStatusError):
        return exc.status_code in _TRANSIENT_STATUS or exc.status_code >= 500
    return False


def _describe(exc: Exception) -> str:
    if isinstance(exc, TimeoutError):
        return f"no reply within {cfg.llm.timeout_s} s"
    status = getattr(exc, "status_code", None)
    return f"{type(exc).__name__}{f' {status}' if status else ''}"


def _seen_ids(messages: list[dict]) -> set[str]:
    return {c["id"] for m in messages if m.get("role") == "assistant" for c in m.get("tool_calls") or ()}


async def next_reply(
    *,
    llm: Any,
    messages: list[dict],
    tools: list[dict],
    run_id: str,
    step: int,
    llm_attempts: int,
    fault: LLMFault | None,
    tracer: Tracer,
) -> Outcome:
    prompt = [{"role": "system", "content": SYSTEM_PROMPT}, *messages]
    tool_names = {t["function"]["name"] for t in tools}
    max_attempts = cfg.llm.max_attempts

    for attempt in range(1, max_attempts + 1):
        faulted = hits(fault, llm_attempts)
        llm_attempts += 1
        started = time.perf_counter()
        try:
            async with asyncio.timeout(cfg.llm.timeout_s):
                if faulted and fault.mode == "timeout":
                    await asyncio.sleep(cfg.llm.timeout_s + 1)  # the real timeout path
                reply = LLMReply(None) if faulted else await llm.complete(prompt, tools)
        except Exception as exc:
            if not isinstance(exc, TimeoutError | openai.APIError):
                raise  # a bug, not an LLM failure: the runner ends the run with internal_error
            reason = _describe(exc)
            retry = _transient(exc) and attempt < max_attempts
            await _llm_event(tracer, run_id, llm, attempt, started, None, "retry" if retry else "unavailable", reason)
            if not retry:
                return Outcome("unavailable", llm_attempts, reason=reason)
            delay = backoff(attempt, cfg.retry.base_delay_s, cfg.retry.max_delay_s)
            await tracer.emit(
                run_id,
                "retry",
                node="agent",
                status="retry",
                attention="warn",
                msg=f"LLM attempt {attempt} failed ({reason}); retrying in {delay:.2f} s",
                data={"attempt": attempt, "delay_s": round(delay, 3), "reason": reason},
            )
            await asyncio.sleep(delay)
            continue

        reason = classify(reply, tool_names, cfg.limits.max_calls_per_reply)
        if reason:
            await _llm_event(tracer, run_id, llm, attempt, started, reply, "malformed", reason)
            return Outcome("malformed", llm_attempts, reason=reason)

        ids = unique_ids(reply.tool_calls, _seen_ids(messages), step)
        message: dict = {"role": "assistant", "content": reply.content}
        if reply.tool_calls:
            message["tool_calls"] = [
                {"id": i, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                for i, c in zip(ids, reply.tool_calls, strict=True)
            ]
        parsed = [
            {"id": i, "name": c.name, "args": json.loads(c.arguments)}
            for i, c in zip(ids, reply.tool_calls, strict=True)
        ]
        kind: Kind = "tool_calls" if parsed else "final"
        await _llm_event(tracer, run_id, llm, attempt, started, reply, kind, None, parsed)
        return Outcome(kind, llm_attempts, message=message, calls=parsed)

    raise AssertionError("unreachable: the last attempt always returns")


async def _llm_event(
    tracer: Tracer,
    run_id: str,
    llm: Any,
    attempt: int,
    started: float,
    reply: LLMReply | None,
    outcome: str,
    reason: str | None,
    calls: list[dict] | None = None,
) -> None:
    attention = {"malformed": "warn", "unavailable": "error"}.get(outcome)
    await tracer.emit(
        run_id,
        "llm",
        node="agent",
        status=outcome,
        attention=attention,
        msg=f"LLM attempt {attempt}: {outcome}" + (f" ({reason})" if reason else ""),
        data={
            "attempt": attempt,
            "model": llm.model,
            "prompt_sha": PROMPT_SHA,
            "prompt_tokens": reply.prompt_tokens if reply else 0,
            "completion_tokens": reply.completion_tokens if reply else 0,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "outcome": outcome,
            "reason": reason,
            # With args, so the UI can show a call before it runs. The tracer masks secrets in them.
            "tool_calls": [{"id": c["id"], "name": c["name"], "args": c["args"]} for c in calls or ()],
        },
    )
