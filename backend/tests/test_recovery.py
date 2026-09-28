import pytest

from app.harness.runner import Conflict, NotFound, Runner
from app.llm.fake import ScriptedLLM, calls, final

INCIDENT = {"title": "payments-api is degraded", "description": "p95 2400 ms, error rate 12%.", "severity": "SEV2"}
STATUS = ("get_service_status", {"service_name": "payments-api"})


class Crash(Exception):
    """Stands in for the process dying at a chosen point."""


async def test_awaiting_approval_untouched(runner):
    llm = ScriptedLLM([calls(("create_incident", INCIDENT, "c0"))])
    paused = await runner.create_run("Open an incident")
    assert await runner.run_segment(paused["id"], llm_client=llm) == "awaiting_approval"
    running = await runner.create_run("Check payments-api")  # created, not finished: as after a crash
    assert await runner.recover() == [running["id"]]
    assert (await runner.store.get_run(paused["id"]))["status"] == "awaiting_approval"
    assert (await runner.store.get_run(running["id"]))["status"] == "interrupted"
    assert await runner.store.list_events(running["id"]) == []  # recovery emits no event


async def test_continue_run_resumes_from_last_checkpoint(runner, monkeypatch, tmp_path):
    # The process dies inside the second LLM turn: the first tool result is already checkpointed.
    llm = ScriptedLLM([calls(STATUS), Crash()])
    run = await runner.create_run("Check payments-api")
    monkeypatch.setattr(runner, "_internal_error", _reraise)
    with pytest.raises(Crash):
        await runner.run_segment(run["id"], llm_client=llm)
    await runner.close()
    reopened = await Runner.open(tmp_path / "harness.db")
    try:
        assert await reopened.recover() == [run["id"]]
        await reopened.request_resume(run["id"], actor="anonymous")
        resumed = ScriptedLLM([final("payments-api is degraded.")])
        assert await reopened.continue_run(run["id"], llm_client=resumed) == "completed"
        tools = [e for e in await reopened.store.list_events(run["id"]) if e["kind"] == "tool"]
        assert len(tools) == 1  # the status call ran once, before the crash
        assert len(resumed.seen) == 1 and resumed.seen[0][-1]["role"] == "tool"
    finally:
        await reopened.close()


async def _reraise(run_id, opts):
    raise Crash  # instead of failing the run: the process "dies" here


async def test_resume_applies_decision_recorded_before_crash(runner, tmp_path):
    llm = ScriptedLLM([calls(("create_incident", INCIDENT, "c0"))])
    run = await runner.create_run("Open an incident")
    await runner.run_segment(run["id"], llm_client=llm)
    [approval] = await runner.store.list_approvals(run_id=run["id"])
    await runner.decide(run["id"], approval["id"], decision="approve", actor="anonymous")
    await runner.close()  # dies before continue_run
    reopened = await Runner.open(tmp_path / "harness.db")
    try:
        assert await reopened.recover() == [run["id"]]  # the decision left it running
        await reopened.request_resume(run["id"], actor="anonymous")
        assert await reopened.continue_run(run["id"], llm_client=ScriptedLLM([final("Opened.")])) == "completed"
        assert len(await reopened.store.list_incidents()) == 1
    finally:
        await reopened.close()


async def test_resume_repauses_when_approval_row_missing(runner, monkeypatch, tmp_path):
    llm = ScriptedLLM([calls(("create_incident", INCIDENT, "c0"))])
    run = await runner.create_run("Open an incident")

    async def crash(*args, **kwargs):
        raise Crash  # the checkpoint holds the interrupt; the approval row is never written

    monkeypatch.setattr(runner.store, "pause_run", crash)
    with pytest.raises(Crash):
        await runner.run_segment(run["id"], llm_client=llm)
    await runner.close()
    reopened = await Runner.open(tmp_path / "harness.db")
    try:
        await reopened.recover()
        await reopened.request_resume(run["id"], actor="anonymous")
        assert await reopened.continue_run(run["id"], llm_client=ScriptedLLM([])) == "awaiting_approval"
        [approval] = await reopened.store.list_approvals(run_id=run["id"])
        assert approval["tool_call_id"] == "c0" and approval["status"] == "pending"
        pending = [e for e in await reopened.store.list_events(run["id"]) if e["kind"] == "approval"]
        assert len(pending) == 1 and pending[0]["data"]["approval_id"] == approval["id"]
    finally:
        await reopened.close()


async def test_recover_returns_ids_oldest_first(runner):
    first = await runner.create_run("first")
    second = await runner.create_run("second")
    assert await runner.recover() == [first["id"], second["id"]]


async def test_decision_on_awaiting_approval_run_works_after_recover(runner):
    llm = ScriptedLLM([calls(("create_incident", INCIDENT, "c0")), final("done")])
    paused = await runner.create_run("Open an incident")
    assert await runner.run_segment(paused["id"], llm_client=llm) == "awaiting_approval"
    running = await runner.create_run("Check payments-api")
    assert await runner.recover() == [running["id"]]  # the paused run's approval is untouched
    [approval] = await runner.store.list_approvals(run_id=paused["id"])
    decided = await runner.decide(paused["id"], approval["id"], decision="approve", actor="anonymous")
    assert decided["status"] == "approved"
    assert await runner.continue_run(paused["id"], llm_client=llm) == "completed"


async def test_resume_from_no_checkpoint_runs_from_start(runner):
    run = await runner.create_run("Check payments-api")
    assert await runner.recover() == [run["id"]]  # interrupted before its first segment ever ran
    assert await runner.request_resume(run["id"], actor="anonymous") == {"run_id": run["id"], "status": "running"}
    llm = ScriptedLLM([calls(STATUS), final("payments-api is degraded.")])
    assert await runner.continue_run(run["id"], llm_client=llm) == "completed"
    assert len(llm.seen) == 2 and llm.seen[0][-1]["role"] == "user"  # the initial state, not a resumed one


async def test_request_resume_needs_interrupted(runner):
    run = await runner.create_run("Check payments-api")
    with pytest.raises(Conflict, match="is running, not interrupted"):
        await runner.request_resume(run["id"], actor="anonymous")
    with pytest.raises(NotFound):
        await runner.request_resume("nope", actor="anonymous")
    await runner.recover()
    assert await runner.request_resume(run["id"], actor="anonymous") == {"run_id": run["id"], "status": "running"}
    audit = (await runner.store.list_events(run["id"]))[-1]
    assert audit["data"] == {"actor": "anonymous", "action": "resume_run", "entity_id": run["id"]}
