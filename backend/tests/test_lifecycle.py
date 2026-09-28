import asyncio
import json
from dataclasses import replace
from functools import partial

import pytest
from conftest import FakeJudge, wait_for_status

from app.config import cfg
from app.eval import metrics
from app.harness.runner import Conflict, NotFound, Runner
from app.llm.fake import ScriptedLLM, calls, final
from app.tools import registry

INCIDENT = {"title": "payments-api is degraded", "description": "p95 2400 ms, error rate 12%.", "severity": "SEV2"}
STATUS = ("get_service_status", {"service_name": "payments-api"})


async def kinds(runner, run_id):
    return [e["kind"] for e in await runner.store.list_events(run_id)]


def hanging_status(monkeypatch):
    """get_service_status waits until the test sets the returned event, or forever."""
    started, release = asyncio.Event(), asyncio.Event()
    tool = registry.TOOLS["get_service_status"]
    real = tool.run

    async def hang(args, ctx):
        started.set()
        await release.wait()
        return await real(args, ctx)

    monkeypatch.setitem(registry.TOOLS, tool.name, replace(tool, run=hang))
    return started, release


async def test_spawned_segments_of_one_run_run_one_at_a_time(runner):
    order = []
    inside = asyncio.Event()

    async def segment(name):
        order.append(f"{name} start")
        inside.set()
        await asyncio.sleep(0.02)
        order.append(f"{name} end")

    first = runner.spawn("r1", partial(segment, "a"))
    await inside.wait()
    second = runner.spawn("r1", partial(segment, "b"))
    other = runner.spawn("r2", partial(segment, "c"))  # another run is not held back
    await asyncio.gather(first, second, other)
    assert order.index("a end") < order.index("b start")
    assert order.index("c start") < order.index("a end")
    assert runner._tasks == {}  # finished tasks are forgotten


async def test_spawned_failure_is_logged(runner, caplog):
    async def boom():
        raise RuntimeError("segment bug")

    await asyncio.gather(runner.spawn("r1", boom), return_exceptions=True)
    await asyncio.sleep(0)  # the done callback
    assert any(r.getMessage() == "background segment failed" and r.exc_info for r in caplog.records)


async def test_decision_waits_for_the_pausing_segment(runner, monkeypatch):
    llm = ScriptedLLM([calls(("create_incident", INCIDENT, "c0")), final("done")])
    run = await runner.create_run("Open an incident", options=None)
    gate, release = asyncio.Event(), asyncio.Event()
    real_emit = runner.tracer.emit

    async def slow_approval_event(run_id, kind, **kwargs):
        if kind == "approval" and kwargs.get("status") == "pending":
            gate.set()
            await release.wait()  # the segment has written the row but not yet its event
        return await real_emit(run_id, kind, **kwargs)

    monkeypatch.setattr(runner.tracer, "emit", slow_approval_event)
    segment = runner.spawn(run["id"], partial(runner.run_segment, run["id"], llm_client=llm))
    await gate.wait()
    [approval] = await runner.store.list_approvals(run_id=run["id"])
    decision = asyncio.ensure_future(runner.decide(run["id"], approval["id"], decision="approve", actor="a"))
    await asyncio.sleep(0.05)
    assert not decision.done()  # it waits for the run's lock
    release.set()
    await asyncio.gather(segment, decision)
    approvals = [e for e in await runner.store.list_events(run["id"]) if e["kind"] == "approval"]
    assert [e["status"] for e in approvals] == ["pending", "approved"]  # the decision follows the pause


async def test_cancel_running_segment_writes_one_done(runner, monkeypatch):
    started, _ = hanging_status(monkeypatch)
    run = await runner.create_run("Check payments-api")
    runner.spawn(run["id"], partial(runner.run_segment, run["id"], llm_client=ScriptedLLM([calls(STATUS)])))
    await started.wait()
    assert await runner.cancel(run["id"], actor="anonymous") == {"run_id": run["id"], "status": "cancelled"}
    row = await runner.store.get_run(run["id"])
    assert row["status"] == "cancelled" and row["finished_at"] and row["steps"] == 1
    events = await runner.store.list_events(run["id"])
    done = [e for e in events if e["kind"] == "done"]
    assert len(done) == 1 and events[-1]["kind"] == "done"
    assert (done[0]["status"], done[0]["attention"]) == ("cancelled", "info")
    assert done[0]["data"] == {"status": "cancelled", "error": None, "steps": 1, "tool_calls": 0}
    assert events[-2]["data"] == {"actor": "anonymous", "action": "cancel_run", "entity_id": run["id"]}
    with pytest.raises(Conflict, match="already cancelled"):
        await runner.cancel(run["id"], actor="anonymous")
    with pytest.raises(NotFound):
        await runner.cancel("nope", actor="anonymous")


async def test_cancel_during_online_evaluation_conflicts(runner, search, monkeypatch):
    judging, release = asyncio.Event(), asyncio.Event()

    class SlowJudge(FakeJudge):
        async def _score(self, metric, *args):
            judging.set()
            await release.wait()
            return await super()._score(metric, *args)

    monkeypatch.setattr(metrics, "get_judge", lambda: SlowJudge())
    run = await runner.create_run("Why is payments-api slow?", options={"evaluate": True})
    runner.start(run["id"])
    await judging.wait()  # the run is final; its segment is scoring it
    with pytest.raises(Conflict, match="already completed"):
        await runner.cancel(run["id"], actor="anonymous")
    release.set()
    for _ in range(250):
        if len(await runner.store.list_evals(run["id"])) == 3:
            break
        await asyncio.sleep(0.02)
    assert len(await runner.store.list_evals(run["id"])) == 3  # the evaluation still finished
    assert (await kinds(runner, run["id"])).count("done") == 1


async def test_close_cancels_running_segments(runner, monkeypatch, tmp_path):
    started, _ = hanging_status(monkeypatch)
    run = await runner.create_run("Check payments-api")
    task = runner.spawn(run["id"], partial(runner.run_segment, run["id"], llm_client=ScriptedLLM([calls(STATUS)])))
    await started.wait()
    await runner.close()
    assert task.cancelled()
    reopened = await type(runner).open(tmp_path / "harness.db")  # the runner fixture's file
    try:
        row = await reopened.store.get_run(run["id"])
        assert row["status"] == "running"  # the next API start marks it interrupted
        assert "done" not in await kinds(reopened, run["id"])
    finally:
        await reopened.close()


async def test_sweep_forever_keeps_going_after_an_error(runner, monkeypatch, caplog):
    sweeps = []

    async def flaky_sweep():
        sweeps.append(1)
        if len(sweeps) == 1:
            raise RuntimeError("database hiccup")
        return 0

    monkeypatch.setattr(runner, "sweep", flaky_sweep)
    loop = asyncio.ensure_future(runner.sweep_forever(0.01))
    for _ in range(100):
        if len(sweeps) >= 3:
            break
        await asyncio.sleep(0.01)
    loop.cancel()
    with pytest.raises(asyncio.CancelledError):
        await loop
    assert len(sweeps) >= 3
    assert [r.getMessage() for r in caplog.records if r.name == "app.runner"] == ["expiry sweep failed"]


async def test_close_with_no_tasks(tmp_path):
    r = await Runner.open(tmp_path / "harness.db")
    await r.close()  # nothing to cancel or gather; must not raise


async def test_spawn_does_not_log_cancelled_error(runner, caplog):
    started = asyncio.Event()

    async def hang():
        started.set()
        await asyncio.Event().wait()  # never released; only a cancel ends it

    task = runner.spawn("r1", hang)
    await started.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert runner._tasks == {}
    assert not any(r.getMessage() == "background segment failed" for r in caplog.records)


async def test_cancel_interrupted_run_writes_one_done(runner):
    run = await runner.create_run("Check payments-api")
    assert await runner.recover() == [run["id"]]  # running -> interrupted before any segment ran
    assert (await runner.store.get_run(run["id"]))["status"] == "interrupted"
    assert await runner.cancel(run["id"], actor="anonymous") == {"run_id": run["id"], "status": "cancelled"}
    row = await runner.store.get_run(run["id"])
    assert row["status"] == "cancelled" and row["steps"] == 0 and row["tool_calls"] == 0
    done = [e for e in await runner.store.list_events(run["id"]) if e["kind"] == "done"]
    assert len(done) == 1


async def test_cancel_awaiting_approval_run_writes_one_done(runner):
    llm = ScriptedLLM([calls(("create_incident", INCIDENT, "c0")), final("done")])
    run = await runner.create_run("Open an incident")
    assert await runner.run_segment(run["id"], llm_client=llm) == "awaiting_approval"
    state = await runner.get_state(run["id"])
    assert await runner.cancel(run["id"], actor="anonymous") == {"run_id": run["id"], "status": "cancelled"}
    row = await runner.store.get_run(run["id"])
    assert row["status"] == "cancelled" and row["steps"] == state["steps"] and row["tool_calls"] == state["tool_calls"]
    done = [e for e in await runner.store.list_events(run["id"]) if e["kind"] == "done"]
    assert len(done) == 1
    [approval] = await runner.store.list_approvals(run_id=run["id"])
    assert approval["status"] == "cancelled"


async def test_cancel_of_decided_segment_queued_behind_the_lock_runs_nothing(runner):
    """A decision was recorded and its `continue_run` spawned, but another task still holds the run's lock."""
    llm = ScriptedLLM([calls(("create_incident", INCIDENT, "c0")), final("done")])
    run = await runner.create_run("Open an incident")
    assert await runner.run_segment(run["id"], llm_client=llm) == "awaiting_approval"
    [approval] = await runner.store.list_approvals(run_id=run["id"])
    await runner.decide(run["id"], approval["id"], decision="approve", actor="a")

    hold, release = asyncio.Event(), asyncio.Event()

    async def block():
        hold.set()
        await release.wait()

    blocker = runner.spawn(run["id"], block)
    await hold.wait()
    segment = runner.spawn(run["id"], partial(runner.continue_run, run["id"], llm_client=llm))
    await asyncio.sleep(0.02)  # queued behind the lock: has not started
    assert not segment.done()
    assert await runner.cancel(run["id"], actor="anonymous") == {"run_id": run["id"], "status": "cancelled"}
    await asyncio.gather(blocker, segment, return_exceptions=True)
    assert segment.cancelled()  # cancelled before it ever ran
    done = [e for e in await runner.store.list_events(run["id"]) if e["kind"] == "done"]
    assert len(done) == 1
    assert await runner.store.list_incidents() == []


async def test_concurrent_cancels_one_wins(runner):
    run = await runner.create_run("Check payments-api")
    results = await asyncio.gather(
        runner.cancel(run["id"], actor="a"), runner.cancel(run["id"], actor="b"), return_exceptions=True
    )
    oks = [r for r in results if not isinstance(r, Exception)]
    conflicts = [r for r in results if isinstance(r, Exception)]
    assert oks == [{"run_id": run["id"], "status": "cancelled"}]
    assert len(conflicts) == 1 and isinstance(conflicts[0], Conflict)
    done = [e for e in await runner.store.list_events(run["id"]) if e["kind"] == "done"]
    assert len(done) == 1


async def test_sweep_races_decision_only_one_wins(runner, monkeypatch):
    monkeypatch.setattr(cfg.approval, "ttl_s", 0)
    llm = ScriptedLLM([calls(("create_incident", INCIDENT, "c0")), final("done")])
    run = await runner.create_run("Open an incident")
    assert await runner.run_segment(run["id"], llm_client=llm) == "awaiting_approval"
    [approval] = await runner.store.list_approvals(run_id=run["id"])
    monkeypatch.setattr(runner, "_client", lambda mode: llm)  # a spawned continue_run picks it up

    decide_task = asyncio.ensure_future(runner.decide(run["id"], approval["id"], decision="approve", actor="a"))
    swept = await runner.sweep()
    try:
        await decide_task
        decided = True
    except Conflict:
        decided = False

    row = await runner.store.get_approval(approval["id"])
    assert (swept, decided) in [(1, False), (0, True)]  # exactly one of them wins
    assert row["status"] in ("approved", "expired")
    approvals = [e for e in await runner.store.list_events(run["id"]) if e["kind"] == "approval"]
    assert len(approvals) == 2  # pending, then the one decision that won: no duplicate


async def test_sweep_expires_two_runs_at_once(runner, monkeypatch):
    monkeypatch.setattr(cfg.approval, "ttl_s", 0)
    run_a = await runner.create_run("Open an incident A")
    run_b = await runner.create_run("Open an incident B")
    for run in (run_a, run_b):
        llm = ScriptedLLM([calls(("create_incident", INCIDENT, "c0")), final("done")])
        assert await runner.run_segment(run["id"], llm_client=llm) == "awaiting_approval"
    monkeypatch.setattr(runner, "_client", lambda mode: ScriptedLLM([final("rejected, moving on")]))
    assert await runner.sweep() == 2
    for run in (run_a, run_b):
        [approval] = await runner.store.list_approvals(run_id=run["id"])
        assert approval["status"] == "expired"
        await wait_for_status(runner, run["id"], "completed")
    assert await runner.store.list_incidents() == []


async def test_sweep_skips_approval_decided_after_expiry(runner, monkeypatch):
    monkeypatch.setattr(cfg.approval, "ttl_s", 0)  # expired the instant it is created
    llm = ScriptedLLM([calls(("create_incident", INCIDENT, "c0")), final("done")])
    run = await runner.create_run("Open an incident")
    assert await runner.run_segment(run["id"], llm_client=llm) == "awaiting_approval"
    [approval] = await runner.store.list_approvals(run_id=run["id"])
    await runner.decide(run["id"], approval["id"], decision="approve", actor="a")  # decided despite ttl_s = 0
    assert await runner.sweep() == 0  # already decided: nothing to do
    row = await runner.store.get_approval(approval["id"])
    assert row["status"] == "approved"


async def test_started_run_reaches_a_final_status(runner, search):
    run = await runner.create_run("Why is payments-api slow?")
    runner.start(run["id"])
    row = await wait_for_status(runner, run["id"], "completed")
    assert row["final"].startswith("payments-api is degraded")
    state = await runner.get_state(run["id"])
    assert json.loads(state["messages"][2]["content"])["ok"]


async def test_sweep_skips_a_busy_run(runner, monkeypatch):
    monkeypatch.setattr(cfg.approval, "ttl_s", 0)
    llm = ScriptedLLM([calls(("create_incident", INCIDENT, "c0")), final("done")])
    run = await runner.create_run("Open an incident")
    await runner.run_segment(run["id"], llm_client=llm)
    monkeypatch.setattr(runner, "_client", lambda mode: llm)
    async with runner._locks[run["id"]]:  # e.g. a decided segment is running
        assert await runner.sweep() == 0  # it does not wait behind the run
    assert await runner.sweep() == 1  # the next sweep takes it
    await wait_for_status(runner, run["id"], "completed")


async def test_start_after_a_cancel_runs_nothing_and_logs_nothing(runner, caplog):
    run = await runner.create_run("Check payments-api")
    await runner.cancel(run["id"], actor="anonymous")  # lands between create and start
    await runner.start(run["id"])
    assert (await runner.store.get_run(run["id"]))["status"] == "cancelled"
    assert not [r for r in caplog.records if r.getMessage() == "background segment failed"]
