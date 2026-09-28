"""The agent loop as a LangGraph graph with our own nodes (ADR 0002, spec: Agent loop).

START -> guard -> agent -> (approval ->) tools -> guard ... -> finalize -> END

Nodes write what routing needs (`error`, `final`, `pending`); routing functions
only read the state. Run-scoped objects (LLM client, store, tracer, limits,
faults) come from the runtime context, which is not checkpointed.
"""

import json
from dataclasses import dataclass
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from app.config import Limits
from app.harness import llm_gateway, tool_gateway
from app.harness.policy import check_calls
from app.harness.state import AgentState, RunStatus, err
from app.harness.store import Store
from app.harness.tracer import Tracer
from app.tools.faults import EmbedCounter, Faults
from app.tools.registry import TOOLS, openai_tools

# The complete list of run error codes and the status each one gives (plan: Final status).
STATUS_BY_ERROR = {
    None: RunStatus.COMPLETED,
    "max_steps": RunStatus.LIMIT_EXCEEDED,
    "max_tool_calls": RunStatus.LIMIT_EXCEEDED,
    "recursion_limit": RunStatus.LIMIT_EXCEEDED,
    "llm_unavailable": RunStatus.FAILED,
    "malformed_reply": RunStatus.FAILED,
    "internal_error": RunStatus.FAILED,
    "max_run_seconds": RunStatus.TIMED_OUT,
}


@dataclass
class RunContext:
    run_id: str
    limits: Limits
    faults: Faults
    llm: Any
    store: Store
    tracer: Tracer


def needs_approval(call: dict) -> bool:
    return call["refusal"] is None and TOOLS[call["name"]].requires_approval


async def _stage(ctx: RunContext, node: str, msg: str, data: dict | None = None) -> None:
    await ctx.tracer.emit(ctx.run_id, "stage", node=node, msg=msg, data=data)


async def guard(state: AgentState, runtime: Runtime[RunContext]) -> dict:
    ctx, limits = runtime.context, runtime.context.limits
    await _stage(
        ctx,
        "guard",
        f"step {state['steps']} of {limits.max_steps}, {state['tool_calls']} of {limits.max_tool_calls} tool calls",
        {
            "steps": state["steps"],
            "max_steps": limits.max_steps,
            "tool_calls": state["tool_calls"],
            "max_tool_calls": limits.max_tool_calls,
        },
    )
    if not state["error"] and state["steps"] >= limits.max_steps:
        return {"error": "max_steps"}
    return {}


async def agent(state: AgentState, runtime: Runtime[RunContext]) -> dict:
    ctx = runtime.context
    step = state["steps"] + 1
    await _stage(ctx, "agent", f"LLM turn {step}")
    outcome = await llm_gateway.next_reply(
        llm=ctx.llm,
        messages=state["messages"],
        tools=openai_tools(),
        run_id=ctx.run_id,
        step=step,
        llm_attempts=state["llm_attempts"],
        fault=ctx.faults.llm,
        tracer=ctx.tracer,
    )
    update: dict = {"steps": step, "llm_attempts": outcome.llm_attempts}
    if outcome.kind == "unavailable":
        return {**update, "error": "llm_unavailable"}
    if outcome.kind == "malformed":
        repairs = state["repairs"] + 1
        if repairs > ctx.limits.max_repairs:
            return {**update, "repairs": repairs, "error": "malformed_reply"}
        # The malformed reply is not kept; the model sees why and tries again.
        return {**update, "repairs": repairs, "messages": [llm_gateway.correction(outcome.reason)]}

    update |= {"repairs": 0, "messages": [outcome.message]}
    if outcome.kind == "final":
        return {**update, "final": outcome.message["content"]}
    # Checks run here, before approval, so a person is never asked about a call that would be refused.
    checked = check_calls(
        outcome.calls,
        limits=ctx.limits,
        tool_calls=state["tool_calls"],
        call_counts=state["call_counts"],
        incidents=state["incidents"],
    )
    update |= {"pending": checked.pending, "call_counts": checked.call_counts}
    if checked.error:
        update["error"] = checked.error
    return update


def after_agent(state: AgentState) -> str:
    if state["error"] in ("llm_unavailable", "malformed_reply") or state["final"] is not None:
        return "finalize"
    if state["pending"]:
        # One reply that mixes approval and other calls waits as a whole.
        return "approval" if any(needs_approval(c) for c in state["pending"]) else "tools"
    return "guard"  # malformed and repaired


async def approval(state: AgentState, runtime: Runtime[RunContext]) -> dict:
    # Nothing before interrupt(): LangGraph runs this node again from the top on resume.
    decisions = dict(state["decisions"])
    for call in state["pending"]:
        if needs_approval(call) and call["id"] not in decisions:
            decisions[call["id"]] = interrupt({"tool_call_id": call["id"], "tool": call["name"], "args": call["args"]})
    return {"decisions": decisions}


async def tools(state: AgentState, runtime: Runtime[RunContext]) -> dict:
    ctx = runtime.context
    await _stage(ctx, "tools", f"{len(state['pending'])} call(s)")
    attempts, tool_calls, incidents = dict(state["tool_attempts"]), state["tool_calls"], state["incidents"]
    embed = EmbedCounter(ctx.faults.embeddings, state["embed_attempts"])  # shared by every call of this node run
    messages = []
    for call in state["pending"]:
        name = call["name"]
        decision = state["decisions"].get(call["id"]) or {}
        if call["refusal"] is not None:
            envelope = call["refusal"]
            await tool_gateway.refused(call, envelope, run_id=ctx.run_id, tracer=ctx.tracer)
        elif decision.get("decision") == "reject":
            # A person said no: the call never runs and the LLM gets the reason.
            envelope = err("rejected", f"{name} was rejected: {decision.get('reason')}")
            await tool_gateway.refused(call, envelope, run_id=ctx.run_id, tracer=ctx.tracer)
        else:
            envelope, made = await tool_gateway.execute(
                call,
                run_id=ctx.run_id,
                decision=decision or None,
                attempts_before=attempts.get(name, 0),
                fault=ctx.faults.for_tool(name),
                store=ctx.store,
                tracer=ctx.tracer,
                embed=embed,
            )
            attempts[name] = attempts.get(name, 0) + made
            tool_calls += made > 0  # refused by the gateway: not an execution
            if name == "create_incident":
                # From the store, so an incident committed before a timeout still counts.
                incidents = len(await ctx.store.list_incidents(ctx.run_id))
        # Every tool_call_id gets exactly one tool message, refused or not.
        messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(envelope)})
    return {
        "messages": messages,
        "pending": [],
        "tool_attempts": attempts,
        "tool_calls": tool_calls,
        "incidents": incidents,
        "embed_attempts": embed.attempts,
    }


async def finalize(state: AgentState, runtime: Runtime[RunContext]) -> dict:
    status = STATUS_BY_ERROR[state["error"]]
    await _stage(runtime.context, "finalize", f"run {status}")
    return {"status": status.value}  # plain str: the checkpoint loads only safe types


def build_graph(checkpointer: Any) -> Any:
    graph = StateGraph(AgentState, context_schema=RunContext)
    for node in (guard, agent, approval, tools, finalize):
        graph.add_node(node.__name__, node)
    graph.add_edge(START, "guard")
    graph.add_conditional_edges("guard", lambda s: "finalize" if s["error"] else "agent", ["finalize", "agent"])
    graph.add_conditional_edges("agent", after_agent, ["finalize", "approval", "tools", "guard"])
    graph.add_edge("approval", "tools")
    graph.add_edge("tools", "guard")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)
