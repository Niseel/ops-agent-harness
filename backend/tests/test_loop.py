import asyncio
import json
from dataclasses import replace

import pytest
from langgraph.checkpoint.base import empty_checkpoint

from app.config import settings
from app.harness.runner import Runner
from app.llm.fake import ScriptedLLM, calls, final, raw
from app.llm.openai_compat import OpenAICompatClient
from app.tools import registry

INCIDENT = {"title": "payments-api is degraded", "description": "p95 2400 ms, error rate 12%.", "severity": "SEV2"}


async def kinds(runner, run_id, kind):
    return [e for e in await runner.store.list_events(run_id) if e["kind"] == kind]


def tool_results(messages):
    return [json.loads(m["content"]) for m in messages if m["role"] == "tool"]


async def test_success_run_completes(runner, search):
    run = await runner.create_run("Why is payments-api slow?")
    assert await runner.run_segment(run["id"]) == "completed"

    row = await runner.store.get_run(run["id"])
    assert (row["status"], row["error"], row["steps"], row["tool_calls"]) == ("completed", None, 3, 2)
    assert row["final"].startswith("payments-api is degraded: p95 2400 ms")
    assert row["finished_at"]

    state = await runner.get_state(run["id"])
    assert [m["role"] for m in state["messages"]] == ["user", "assistant", "tool", "assistant", "tool", "assistant"]
    names = [c["function"]["name"] for m in state["messages"] if m.get("tool_calls") for c in m["tool_calls"]]
    assert names == ["search_knowledge_base", "get_service_status"]
    assert all(r["ok"] for r in tool_results(state["messages"]))
    assert state["status"] == "completed" and state["pending"] == []


async def test_incident_call_pauses_run_without_incident(runner, search):
    run = await runner.create_run("payments-api is slow. Open an incident if it is degraded.")
    assert await runner.run_segment(run["id"]) == "awaiting_approval"

    row = await runner.store.get_run(run["id"])
    assert (row["status"], row["finished_at"]) == ("awaiting_approval", None)
    assert await runner.store.list_incidents() == []
    assert await kinds(runner, run["id"], "done") == []
    [approval] = await kinds(runner, run["id"], "approval")
    assert (approval["status"], approval["attention"], approval["tool"]) == ("pending", "warn", "create_incident")
    data = approval["data"]
    assert data["tool"] == "create_incident" and data["tool_call_id"] and data["interrupt_id"]
    assert data["args"]["severity"] == "SEV2" and data["args"]["title"] == "payments-api is degraded"
    state = await runner.get_state(run["id"])
    assert [p["name"] for p in state["pending"]] == ["create_incident"]


async def test_injected_instruction_stops_at_approval(runner, search):
    run = await runner.create_run("SMS alerts from notifications-worker are delayed. Check the SMS vendor note.")
    assert await runner.run_segment(run["id"]) == "awaiting_approval"
    [approval] = await kinds(runner, run["id"], "approval")
    assert approval["data"]["args"]["severity"] == "SEV1"
    assert await runner.store.list_incidents() == []


async def test_mixed_reply_waits_for_approval(runner):
    llm = ScriptedLLM([calls(("get_service_status", {"service_name": "payments-api"}), ("create_incident", INCIDENT))])
    run = await runner.create_run("Check payments-api and open an incident")
    assert await runner.run_segment(run["id"], llm_client=llm) == "awaiting_approval"
    assert await kinds(runner, run["id"], "tool") == []  # the status call waits too
    state = await runner.get_state(run["id"])
    assert [p["name"] for p in state["pending"]] == ["get_service_status", "create_incident"]


async def test_invalid_incident_args_do_not_pause(runner):
    llm = ScriptedLLM(
        [calls(("create_incident", {**INCIDENT, "severity": "SEV9"})), final("Could not open the incident.")]
    )
    run = await runner.create_run("Open an incident for payments-api")
    assert await runner.run_segment(run["id"], llm_client=llm) == "completed"
    assert await kinds(runner, run["id"], "approval") == []
    state = await runner.get_state(run["id"])
    [result] = tool_results(state["messages"])
    assert result["error"]["type"] == "validation"
    [event] = await kinds(runner, run["id"], "tool")
    assert (event["status"], event["attention"], event["data"]["attempt"]) == ("validation", "warn", 0)
    assert (await runner.store.get_run(run["id"]))["tool_calls"] == 0  # refused calls are not executions


async def test_state_survives_restart(runner, search, tmp_path):
    run = await runner.create_run("Why is payments-api slow?", options={"limits": {"max_steps": 5}})
    await runner.run_segment(run["id"])
    before_row = await runner.store.get_run(run["id"])
    before_events = await runner.store.list_events(run["id"])
    before_messages = (await runner.get_state(run["id"]))["messages"]
    await runner.close()  # a second close by the fixture is a no-op

    reopened = await Runner.open(tmp_path / "harness.db")  # like a restarted process
    try:
        assert await reopened.store.get_run(run["id"]) == before_row
        assert before_row["options"]["limits"]["max_steps"] == 5
        assert await reopened.store.list_events(run["id"]) == before_events
        assert (await reopened.get_state(run["id"]))["messages"] == before_messages
    finally:
        await reopened.close()


async def test_unexpected_exception_fails_run(runner):
    llm = ScriptedLLM([calls(("get_service_status", {"service_name": "payments-api"}))])  # then runs out
    run = await runner.create_run("Check payments-api")
    assert await runner.run_segment(run["id"], llm_client=llm) == "failed"

    row = await runner.store.get_run(run["id"])
    assert (row["status"], row["error"], row["steps"], row["tool_calls"]) == ("failed", "internal_error", 1, 1)
    [error] = await kinds(runner, run["id"], "error")
    assert (error["msg"], error["attention"]) == ("unexpected error, see the log", "error")
    assert "ScriptedLLM" not in json.dumps(error)
    events = await runner.store.list_events(run["id"])
    assert [e["kind"] for e in events[-2:]] == ["error", "done"]
    assert events[-1]["data"] == {"status": "failed", "error": "internal_error", "steps": 1, "tool_calls": 1}


async def test_refused_and_allowed_calls_each_get_one_tool_message(runner):
    bad_incident = {**INCIDENT, "severity": "SEV9"}
    llm = ScriptedLLM(
        [
            calls(("create_incident", bad_incident), ("get_service_status", {"service_name": "payments-api"})),
            final("done"),
        ]
    )
    run = await runner.create_run("Check payments-api")
    assert await runner.run_segment(run["id"], llm_client=llm) == "completed"
    state = await runner.get_state(run["id"])
    tool_messages = [m for m in state["messages"] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_messages] == ["c0", "c1"]  # one message per call, in reply order
    envelopes = tool_results(state["messages"])
    assert envelopes[0]["error"]["type"] == "validation"
    assert envelopes[1]["ok"] is True
    row = await runner.store.get_run(run["id"])
    assert row["tool_calls"] == 1  # the refused call is not an execution


async def test_max_tool_calls_blocked_call_skips_approval(runner):
    llm = ScriptedLLM([calls(("get_service_status", {"service_name": "payments-api"}), ("create_incident", INCIDENT))])
    run = await runner.create_run("Check payments-api and open an incident", options={"limits": {"max_tool_calls": 1}})
    assert await runner.run_segment(run["id"], llm_client=llm) == "limit_exceeded"
    assert await kinds(runner, run["id"], "approval") == []  # the blocked call never asks for a decision
    row = await runner.store.get_run(run["id"])
    assert (row["error"], row["tool_calls"]) == ("max_tool_calls", 1)
    state = await runner.get_state(run["id"])
    status_result, incident_result = tool_results(state["messages"])
    assert status_result["ok"] is True
    assert incident_result["error"]["type"] == "blocked"


async def test_approval_pause_has_no_tool_or_incident_side_effects(runner):
    llm = ScriptedLLM([calls(("create_incident", INCIDENT))])
    run = await runner.create_run("Open an incident for payments-api")
    assert await runner.run_segment(run["id"], llm_client=llm) == "awaiting_approval"
    assert await kinds(runner, run["id"], "tool") == []
    assert await runner.store.list_incidents() == []
    assert await kinds(runner, run["id"], "done") == []


async def test_openai_client_built_once_per_runner(runner, monkeypatch):
    built = []

    class FakeOpenAIClient:
        model = "fake-openai"

        async def complete(self, messages, tools):
            return final("done")

    @classmethod
    def from_settings(cls):
        client = FakeOpenAIClient()
        built.append(client)
        return client

    monkeypatch.setattr(OpenAICompatClient, "from_settings", from_settings)
    run1 = await runner.create_run("Check payments-api", llm="openai")
    run2 = await runner.create_run("Check payments-api again", llm="openai")
    assert await runner.run_segment(run1["id"]) == "completed"
    assert await runner.run_segment(run2["id"]) == "completed"
    assert len(built) == 1  # the second segment reuses the same client, no network involved


@pytest.mark.parametrize(
    "kwargs",
    [
        {"objective": ""},
        {"objective": "   "},
        {"objective": "x" * 2001},
        {"objective": "Check payments-api", "llm": "claude"},
    ],
)
async def test_create_run_rejects_bad_input(runner, kwargs):
    with pytest.raises(ValueError):
        await runner.create_run(**kwargs)


async def test_create_run_refuses_faults_when_fault_injection_is_off(runner, monkeypatch):
    monkeypatch.setattr(settings, "allow_fault_injection", False)
    with pytest.raises(ValueError):
        await runner.create_run("Check payments-api", options={"faults": {"get_service_status": {"mode": "timeout"}}})


async def test_run_segment_unknown_run_raises_lookup_error(runner):
    with pytest.raises(LookupError):
        await runner.run_segment("does-not-exist")


async def test_tool_attempts_and_incidents_in_checkpoint(runner, search):
    run = await runner.create_run("Why is payments-api slow?")
    await runner.run_segment(run["id"])
    state = await runner.get_state(run["id"])
    assert state["tool_attempts"] == {"search_knowledge_base": 1, "get_service_status": 1}
    assert state["incidents"] == 0  # create_incident never ran (no decisions in M1)


@pytest.mark.parametrize(
    ("items", "limits", "status"),
    [
        ([final("done")], None, "completed"),
        ([raw(), raw(), raw()], None, "failed"),
        ([calls(("get_service_status", {"service_name": "payments-api"}))], {"max_steps": 1}, "limit_exceeded"),
    ],
)
async def test_finished_at_set_for_final_statuses(runner, items, limits, status):
    run = await runner.create_run("Check payments-api", options={"limits": limits} if limits else None)
    assert await runner.run_segment(run["id"], llm_client=ScriptedLLM(items)) == status
    row = await runner.store.get_run(run["id"])
    assert row["finished_at"]


async def test_node_timeout_error_ends_internal_error_not_timed_out(runner, monkeypatch):
    """A TimeoutError raised inside a node, not from the segment deadline, is a bug: internal_error."""
    from app.harness import loop as loop_module

    def boom(*args, **kwargs):
        raise TimeoutError("not the segment deadline")

    monkeypatch.setattr(loop_module, "check_calls", boom)
    llm = ScriptedLLM([calls(("get_service_status", {"service_name": "payments-api"}))])
    run = await runner.create_run("Check payments-api")  # default max_run_seconds, well above this instant failure
    assert await runner.run_segment(run["id"], llm_client=llm) == "failed"
    row = await runner.store.get_run(run["id"])
    assert row["error"] == "internal_error"


async def test_outer_cancellation_propagates_and_writes_no_done_event(runner, monkeypatch):
    started = asyncio.Event()

    async def hang(args, ctx):
        started.set()
        await asyncio.sleep(10)

    tool = registry.TOOLS["get_service_status"]
    monkeypatch.setitem(registry.TOOLS, tool.name, replace(tool, run=hang))
    llm = ScriptedLLM([calls(("get_service_status", {"service_name": "payments-api"}))])
    run = await runner.create_run("Check payments-api")
    task = asyncio.ensure_future(runner.run_segment(run["id"], llm_client=llm))
    await asyncio.wait_for(started.wait(), 5)  # the segment is inside the hanging tool
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await kinds(runner, run["id"], "done") == []


async def test_secrets_never_appear_in_run_events(tmp_path, monkeypatch, search):
    secret = "sk-test-leak-0102030405"
    monkeypatch.setattr(settings, "llm_default", "fake")
    monkeypatch.setattr(settings, "allow_fault_injection", True)
    monkeypatch.setattr(settings, "llm_api_key", secret)  # captured by the tracer when the runner opens
    r = await Runner.open(tmp_path / "harness.db")
    try:
        run = await r.create_run(f"Check payments-api using key {secret}")
        await r.run_segment(run["id"])
        blob = json.dumps(await r.store.list_events(run["id"]))
        assert secret not in blob
        assert "***" in blob
    finally:
        await r.close()


async def test_cancelled_checkpoint_write_leaves_no_open_transaction(runner, monkeypatch):
    # A checkpoint write cancelled after its INSERT but before its commit must not lock the file.
    conn, parked = runner._saver_conn, asyncio.Event()

    async def parked_commit():
        parked.set()
        await asyncio.sleep(10)

    monkeypatch.setattr(conn, "commit", parked_commit)
    config = {"configurable": {"thread_id": "t1", "checkpoint_ns": ""}}
    task = asyncio.ensure_future(runner.graph.checkpointer.aput(config, empty_checkpoint(), {}, {}))
    await asyncio.wait_for(parked.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not conn.in_transaction
    async with asyncio.timeout(1):  # "database is locked" would take the 5 s busy timeout
        await runner.create_run("Check payments-api")


async def test_run_segment_runs_a_run_only_once(runner):
    run = await runner.create_run("Check payments-api")
    assert await runner.run_segment(run["id"], llm_client=ScriptedLLM([final("done")])) == "completed"
    with pytest.raises(ValueError, match="already started"):
        await runner.run_segment(run["id"], llm_client=ScriptedLLM([final("again")]))
    done = await kinds(runner, run["id"], "done")
    assert len(done) == 1
