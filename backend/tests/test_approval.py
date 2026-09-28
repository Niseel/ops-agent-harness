import asyncio
import json
from datetime import datetime

import pytest
from conftest import wait_for_status

from app.clock import now_iso
from app.config import cfg
from app.harness.runner import Conflict, NotFound, Runner, resume_value
from app.llm.fake import ScriptedLLM, calls, final

INCIDENT = {"title": "payments-api is degraded", "description": "p95 2400 ms, error rate 12%.", "severity": "SEV2"}
STATUS = ("get_service_status", {"service_name": "payments-api"})


def incident_call(id="c0", **args):
    return calls(("create_incident", {**INCIDENT, **args}, id))


async def events(runner, run_id, kind=None):
    return [e for e in await runner.store.list_events(run_id) if kind is None or e["kind"] == kind]


def tool_results(state):
    return [json.loads(m["content"]) for m in state["messages"] if m["role"] == "tool"]


async def paused(runner, items, objective="Check payments-api and open an incident", **options):
    """A run with a scripted LLM, paused at its first approval. Returns (run_id, llm, pending approval)."""
    llm = ScriptedLLM(items)
    run = await runner.create_run(objective, options=options or None)
    assert await runner.run_segment(run["id"], llm_client=llm) == "awaiting_approval"
    [approval] = await runner.store.list_approvals(run_id=run["id"], status="pending")
    return run["id"], llm, approval


async def test_incident_not_created_before_approval(runner, search):
    run = await runner.create_run("payments-api is slow. Open an incident if it is degraded.")
    before = now_iso()
    assert await runner.run_segment(run["id"]) == "awaiting_approval"
    [approval] = await runner.store.list_approvals(run_id=run["id"])
    assert approval["status"] == "pending" and approval["tool"] == "create_incident"
    assert approval["args"]["severity"] == "SEV2" and approval["created_at"] >= before
    ttl = cfg.approval.ttl_s
    created, expires = approval["created_at"], approval["expires_at"]
    assert (datetime.fromisoformat(expires) - datetime.fromisoformat(created)).total_seconds() == ttl
    assert await runner.store.list_incidents() == []
    assert (await runner.store.get_run(run["id"]))["status"] == "awaiting_approval"


async def test_invalid_incident_args_ask_no_approval(runner):
    llm = ScriptedLLM([incident_call(severity="SEV9"), final("Could not open the incident.")])
    run = await runner.create_run("Open an incident for payments-api")
    assert await runner.run_segment(run["id"], llm_client=llm) == "completed"
    assert await runner.store.list_approvals(run_id=run["id"]) == []
    assert await events(runner, run["id"], "approval") == []
    [result] = tool_results(await runner.get_state(run["id"]))
    assert result["error"]["type"] == "validation"


async def test_approve_creates_one_incident(runner):
    run_id, llm, approval = await paused(runner, [incident_call(), final("Opened the incident.")])
    decided = await runner.decide(run_id, approval["id"], decision="approve", actor="anonymous")
    assert decided["status"] == "approved" and decided["decided_by"] == "anonymous"
    assert (await runner.store.get_run(run_id))["status"] == "running"
    assert await runner.continue_run(run_id, llm_client=llm) == "completed"
    [incident] = await runner.store.list_incidents()
    assert {k: incident[k] for k in INCIDENT} == INCIDENT and incident["run_id"] == run_id
    assert incident["idempotency_key"] == f"{run_id}:c0"
    [tool] = await events(runner, run_id, "tool")
    assert tool["attention"] == "success"


async def test_reject_sends_reason_to_llm(runner):
    run_id, llm, approval = await paused(runner, [incident_call(), final("No incident.")])
    await runner.decide(run_id, approval["id"], decision="reject", reason="not customer facing", actor="anonymous")
    assert await runner.continue_run(run_id, llm_client=llm) == "completed"
    assert await runner.store.list_incidents() == []
    last_input = llm.seen[-1][-1]
    envelope = json.loads(last_input["content"])
    assert last_input["role"] == "tool" and envelope["error"]["type"] == "rejected"
    assert envelope["error"]["message"] == "create_incident was rejected: not customer facing"
    assert envelope["error"]["retryable"] is False
    [tool] = await events(runner, run_id, "tool")
    assert (tool["status"], tool["attention"], tool["data"]["attempt"]) == ("rejected", "info", 0)
    state = await runner.get_state(run_id)
    assert state["tool_calls"] == 0 and state["incidents"] == 0  # a rejected call is not an execution


async def test_edit_uses_new_args(runner):
    run_id, llm, approval = await paused(runner, [incident_call(), final("Opened.")])
    edited = {**INCIDENT, "title": "payments-api errors", "severity": "SEV1"}
    row = await runner.decide(run_id, approval["id"], decision="edit", args=edited, actor="anonymous")
    assert row["status"] == "edited" and row["decision"] == edited
    assert await runner.continue_run(run_id, llm_client=llm) == "completed"
    [incident] = await runner.store.list_incidents()
    assert {k: incident[k] for k in edited} == edited


async def test_invalid_edit_keeps_approval_pending(runner):
    run_id, llm, approval = await paused(runner, [incident_call()])
    with pytest.raises(ValueError, match="severity"):
        await runner.decide(run_id, approval["id"], decision="edit", args={**INCIDENT, "severity": "SEV9"}, actor="a")
    with pytest.raises(ValueError):
        await runner.decide(run_id, approval["id"], decision="edit", args=None, actor="a")
    assert (await runner.store.get_approval(approval["id"]))["status"] == "pending"
    assert (await runner.store.get_run(run_id))["status"] == "awaiting_approval"


async def test_second_decision_conflicts(runner):
    run_id, llm, approval = await paused(runner, [incident_call(), final("done")])
    await runner.decide(run_id, approval["id"], decision="approve", actor="a")
    with pytest.raises(Conflict, match="is approved"):
        await runner.decide(run_id, approval["id"], decision="reject", reason="late", actor="b")


async def test_concurrent_decisions_one_wins(runner):
    run_id, llm, approval = await paused(runner, [incident_call(), final("done")])
    results = await asyncio.gather(
        runner.decide(run_id, approval["id"], decision="approve", actor="a"),
        runner.decide(run_id, approval["id"], decision="reject", reason="no", actor="b"),
        return_exceptions=True,
    )
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, Conflict) for r in results) == 1
    assert len(await events(runner, run_id, "log")) == 1  # one audit event, for the winner


async def test_decide_checks_in_order(runner):
    run_id, llm, approval = await paused(runner, [incident_call()])
    with pytest.raises(NotFound):
        await runner.decide(run_id, "nope", decision="approve", actor="a")
    with pytest.raises(NotFound):
        await runner.decide("other-run", approval["id"], decision="approve", actor="a")
    with pytest.raises(ValueError, match="500"):
        await runner.decide(run_id, approval["id"], decision="reject", reason="x" * 501, actor="a")
    with pytest.raises(ValueError):
        await runner.decide(run_id, approval["id"], decision="maybe", actor="a")
    with pytest.raises(ValueError, match="reason"):
        await runner.decide(run_id, approval["id"], decision="reject", reason="  ", actor="a")
    await runner.store.update_run(run_id, status="interrupted")  # a run that does not wait any more
    with pytest.raises(Conflict, match="is interrupted"):
        await runner.decide(run_id, approval["id"], decision="edit", args={"bad": 1}, actor="a")  # 409 before 422


async def test_incident_cap_blocks_without_approval(runner):
    second = {**INCIDENT, "title": "payments-api is still degraded"}
    llm = ScriptedLLM([incident_call(), calls(("create_incident", second, "c1")), final("done")])
    run = await runner.create_run("Check payments-api and open an incident")
    assert await runner.run_segment(run["id"], llm_client=llm) == "awaiting_approval"
    [approval] = await runner.store.list_approvals(run_id=run["id"])
    await runner.decide(run["id"], approval["id"], decision="approve", actor="a")
    assert await runner.continue_run(run["id"], llm_client=llm) == "completed"
    assert len(await runner.store.list_approvals(run_id=run["id"])) == 1  # no second approval asked
    assert len(await runner.store.list_incidents()) == 1
    results = tool_results(await runner.get_state(run["id"]))
    assert results[1]["error"]["type"] == "blocked" and "incident limit" in results[1]["error"]["message"]


async def test_two_approvals_in_one_reply_asked_in_order(runner, monkeypatch):
    monkeypatch.setattr(cfg.limits, "max_incidents_per_run", 2)
    second = {**INCIDENT, "title": "orders-db is down", "severity": "SEV1"}
    reply = calls(("create_incident", INCIDENT, "c0"), ("create_incident", second, "c1"))
    run_id, llm, first = await paused(runner, [reply, final("done")], limits={"max_incidents_per_run": 2})
    assert first["tool_call_id"] == "c0"
    assert len(await runner.store.list_approvals(run_id=run_id)) == 1  # one pending row per pause
    await runner.decide(run_id, first["id"], decision="approve", actor="a")
    assert await runner.continue_run(run_id, llm_client=llm) == "awaiting_approval"
    assert await events(runner, run_id, "tool") == []  # nothing ran before the second decision
    [second_row] = await runner.store.list_approvals(run_id=run_id, status="pending")
    assert second_row["tool_call_id"] == "c1"
    await runner.decide(run_id, second_row["id"], decision="reject", reason="duplicate", actor="a")
    assert await runner.continue_run(run_id, llm_client=llm) == "completed"
    assert len(await runner.store.list_incidents()) == 1
    assert [e["status"] for e in await events(runner, run_id, "tool")] == ["ok", "rejected"]


async def test_mixed_reply_runs_all_calls_after_approval(runner):
    reply = calls(("get_service_status", STATUS[1], "c0"), ("create_incident", INCIDENT, "c1"))
    run_id, llm, approval = await paused(runner, [reply, final("done")])
    assert await events(runner, run_id, "tool") == []
    await runner.decide(run_id, approval["id"], decision="approve", actor="a")
    assert await runner.continue_run(run_id, llm_client=llm) == "completed"
    assert [e["tool"] for e in await events(runner, run_id, "tool")] == ["get_service_status", "create_incident"]
    assert [r["ok"] for r in tool_results(await runner.get_state(run_id))] == [True, True]


async def test_approval_event_has_id_expiry_and_run_error(runner):
    reply = calls(("create_incident", INCIDENT, "c0"), ("get_service_status", STATUS[1], "c1"))
    run_id, llm, approval = await paused(runner, [reply], limits={"max_tool_calls": 1})
    [event] = await events(runner, run_id, "approval")
    data = event["data"]
    assert (data["approval_id"], data["expires_at"], data["tool_call_id"]) == (
        approval["id"],
        approval["expires_at"],
        "c0",
    )
    assert data["run_error"] == "max_tool_calls" and data["interrupt_id"] and data["args"] == INCIDENT
    await runner.decide(run_id, approval["id"], decision="approve", actor="a")
    assert await runner.continue_run(run_id, llm_client=llm) == "limit_exceeded"
    assert len(await runner.store.list_incidents()) == 1  # the approved call still ran
    row = await runner.store.get_run(run_id)
    assert (row["error"], row["tool_calls"]) == ("max_tool_calls", 1)


async def test_decide_emits_approval_and_audit_events(runner):
    run_id, llm, approval = await paused(runner, [incident_call(), final("done")])
    await runner.decide(run_id, approval["id"], decision="reject", reason="not needed", actor="anonymous")
    decision, audit = (await runner.store.list_events(run_id))[-2:]
    assert (decision["kind"], decision["status"], decision["attention"]) == ("approval", "rejected", "info")
    assert decision["msg"] == "create_incident rejected by anonymous: not needed"
    assert decision["data"] == {
        "approval_id": approval["id"],
        "tool_call_id": "c0",
        "decision": {"decision": "reject", "reason": "not needed"},
        "decided_by": "anonymous",
    }
    assert (audit["kind"], audit["node"], audit["status"], audit["attention"]) == ("log", None, None, None)
    assert audit["data"] == {"actor": "anonymous", "action": "decide_approval", "entity_id": approval["id"]}
    assert audit["msg"] == f"anonymous decide_approval {approval['id']}"


async def test_approved_event_has_no_attention(runner):
    run_id, llm, approval = await paused(runner, [incident_call(), final("done")])
    await runner.decide(run_id, approval["id"], decision="approve", actor="a")
    decision = (await events(runner, run_id, "approval"))[-1]
    assert (decision["status"], decision["attention"], decision["msg"]) == (
        "approved",
        None,
        "create_incident approved by a",
    )


async def test_resume_duplicates_no_event_or_row(runner):
    run_id, llm, approval = await paused(runner, [incident_call(), final("done")])
    # The process died between the checkpoint and the approval row: the row is gone, the run is running.
    await runner.store._db.execute("DELETE FROM approvals WHERE id = ?", (approval["id"],))
    await runner.store.update_run(run_id, status="running")
    assert await runner.continue_run(run_id, llm_client=llm) == "awaiting_approval"
    [again] = await runner.store.list_approvals(run_id=run_id)
    assert again["tool_call_id"] == "c0" and again["status"] == "pending"
    assert len(await events(runner, run_id, "approval")) == 2  # the first pause and this one
    # Once the run waits, another continue_run runs nothing and asks nothing.
    assert await runner.continue_run(run_id, llm_client=llm) == "awaiting_approval"
    # A pause that repeats for a call that already has its row emits nothing new.
    await runner.store.update_run(run_id, status="running")
    assert await runner.continue_run(run_id, llm_client=llm) == "awaiting_approval"
    assert len(await runner.store.list_approvals(run_id=run_id)) == 1
    assert len(await events(runner, run_id, "approval")) == 2


async def test_decision_survives_a_crash_before_the_resume(runner, tmp_path):
    run_id, llm, approval = await paused(runner, [incident_call(), final("done")])
    await runner.decide(run_id, approval["id"], decision="approve", actor="a")
    await runner.close()  # the process dies before it resumes
    reopened = await Runner.open(tmp_path / "harness.db")
    try:
        assert await reopened.continue_run(run_id, llm_client=ScriptedLLM([final("done")])) == "completed"
        assert len(await reopened.store.list_incidents()) == 1
    finally:
        await reopened.close()


async def test_continue_run_on_a_final_run_runs_nothing(runner):
    run_id, llm, approval = await paused(runner, [incident_call(), final("done")])
    await runner.store.cancel_run(run_id, decided_by="a")
    assert await runner.continue_run(run_id, llm_client=llm) == "cancelled"
    with pytest.raises(NotFound):
        await runner.continue_run("nope")


async def test_edit_extra_key_or_non_dict_args_keeps_approval_pending(runner):
    run_id, llm, approval = await paused(runner, [incident_call()])
    with pytest.raises(ValueError):
        await runner.decide(run_id, approval["id"], decision="edit", args={**INCIDENT, "extra": "nope"}, actor="a")
    with pytest.raises(ValueError):
        await runner.decide(run_id, approval["id"], decision="edit", args=["not", "a", "dict"], actor="a")
    assert (await runner.store.get_approval(approval["id"]))["status"] == "pending"
    assert (await runner.store.get_run(run_id))["status"] == "awaiting_approval"


async def test_edited_args_stored_as_the_model_dump(runner):
    run_id, llm, approval = await paused(runner, [incident_call(), final("done")])
    # Given out of the model's field order (severity, title, description): the stored decision comes back
    # in the model's own order, proving it is the validated model's dump and not the raw dict.
    edited = {"severity": "SEV1", "title": "payments-api errors", "description": "New description text here."}
    row = await runner.decide(run_id, approval["id"], decision="edit", args=edited, actor="a")
    assert row["decision"] == edited
    assert list((await runner.store.get_approval(approval["id"]))["decision"].keys()) == [
        "title",
        "description",
        "severity",
    ]


async def test_decide_on_approval_of_another_run_gives_not_found(runner):
    run_id, llm, approval = await paused(runner, [incident_call(), final("done")])
    other = await runner.create_run("Check orders-db")
    with pytest.raises(NotFound):
        await runner.decide(other["id"], approval["id"], decision="approve", actor="a")


async def test_reject_reaches_llm_exactly_once(runner):
    run_id, llm, approval = await paused(runner, [incident_call(), final("No incident.")])
    assert len(llm.seen) == 1  # the reply that proposed the incident
    await runner.decide(run_id, approval["id"], decision="reject", reason="not needed", actor="a")
    assert await runner.continue_run(run_id, llm_client=llm) == "completed"
    assert len(llm.seen) == 2  # exactly one more turn, for the final answer


async def test_decision_and_audit_events_come_before_the_resumed_segment(runner):
    run_id, llm, approval = await paused(runner, [incident_call(), final("done")])
    await runner.decide(run_id, approval["id"], decision="approve", actor="a")
    before = await runner.store.list_events(run_id)
    decision, audit = before[-2], before[-1]
    assert decision["kind"] == "approval" and audit["kind"] == "log"
    assert await runner.continue_run(run_id, llm_client=llm) == "completed"
    after = await runner.store.list_events(run_id)
    resumed = after[len(before) :]
    assert resumed  # the resumed segment emitted at least one event
    assert all(e["seq"] > audit["seq"] > decision["seq"] for e in resumed)


async def test_continue_run_with_no_checkpoint_runs_from_initial_state(runner):
    llm = ScriptedLLM([final("done")])
    run = await runner.create_run("Check payments-api")
    # No run_segment yet: the process died before the first step, so there is no checkpoint at all.
    assert await runner.continue_run(run["id"], llm_client=llm) == "completed"
    assert len(llm.seen) == 1


async def test_graph_finished_before_its_row_was_written_gives_one_done(runner, monkeypatch):
    # The process dies after the graph's last checkpoint but before the final row and `done`.
    run_id, llm, approval = await paused(runner, [incident_call(), final("done")])
    await runner.decide(run_id, approval["id"], decision="approve", actor="a")
    original = runner.store.finish_run

    class Crash(Exception):
        pass

    async def crash(*args, **kwargs):
        raise Crash

    monkeypatch.setattr(runner.store, "finish_run", crash)
    with pytest.raises(Crash):
        await runner.continue_run(run_id, llm_client=llm)
    assert (await runner.store.get_run(run_id))["status"] == "running"
    assert await events(runner, run_id, "done") == []
    monkeypatch.setattr(runner.store, "finish_run", original)
    # The next resume finds a finished graph: no LLM call, one final row and one `done`.
    assert await runner.continue_run(run_id, llm_client=ScriptedLLM([])) == "completed"
    assert len(await events(runner, run_id, "done")) == 1
    assert (await runner.store.get_run(run_id))["final"] == "done"


async def test_finish_after_cancel_win_writes_no_done_or_eval(runner, monkeypatch):
    run_id, llm, approval = await paused(runner, [incident_call(), final("done")])
    await runner.decide(run_id, approval["id"], decision="approve", actor="a")
    original = runner.store.finish_run

    async def cancel_then_finish(*args, **kwargs):
        await runner.store.cancel_run(run_id, decided_by="system")  # wins the race just before the write
        return await original(*args, **kwargs)

    monkeypatch.setattr(runner.store, "finish_run", cancel_then_finish)
    assert await runner.continue_run(run_id, llm_client=llm) == "cancelled"
    assert await events(runner, run_id, "done") == []
    assert await events(runner, run_id, "eval") == []


async def test_expired_resume_value_rejects(runner):
    run_id, llm, approval = await paused(runner, [incident_call(), final("done")])
    await runner.store._db.execute("UPDATE approvals SET status = 'expired' WHERE id = ?", (approval["id"],))
    await runner.store.update_run(run_id, status="running")  # the run still waits; only the row is set expired
    assert await runner.continue_run(run_id, llm_client=llm) == "completed"
    assert await runner.store.list_incidents() == []
    last_input = llm.seen[-1][-1]
    envelope = json.loads(last_input["content"])
    assert envelope["error"]["message"] == "create_incident was rejected: approval expired"


async def test_decision_after_expiry_before_sweep_is_accepted(runner, monkeypatch):
    monkeypatch.setattr(cfg.approval, "ttl_s", 0)
    run_id, llm, approval = await paused(runner, [incident_call(), final("done")])
    assert approval["expires_at"] <= now_iso()  # already expired, but no sweep has run yet
    decided = await runner.decide(run_id, approval["id"], decision="approve", actor="a")
    assert decided["status"] == "approved"


async def test_fakeplanner_end_to_end_with_real_search(runner, search):
    run = await runner.create_run("payments-api is degraded. Please check it and open an incident.")
    assert await runner.run_segment(run["id"]) == "awaiting_approval"
    [approval] = await runner.store.list_approvals(run_id=run["id"])
    assert approval["tool"] == "create_incident"
    await runner.decide(run["id"], approval["id"], decision="approve", actor="a")
    assert await runner.continue_run(run["id"]) == "completed"
    [incident] = await runner.store.list_incidents()
    row = await runner.store.get_run(run["id"])
    assert incident["id"] in row["final"]


def test_resume_values():
    row = {"id": "a", "decision": None, "reason": None}
    assert resume_value({**row, "status": "approved"}) == {"decision": "approve"}
    assert resume_value({**row, "status": "edited", "decision": INCIDENT}) == {"decision": "edit", "args": INCIDENT}
    assert resume_value({**row, "status": "rejected", "reason": "no"}) == {"decision": "reject", "reason": "no"}
    assert resume_value({**row, "status": "expired"}) == {"decision": "reject", "reason": "approval expired"}
    with pytest.raises(ValueError):
        resume_value({**row, "status": "pending"})


async def test_not_found_comes_before_conflict(runner):
    run_id, llm, approval = await paused(runner, [incident_call(), final("done")])
    await runner.decide(run_id, approval["id"], decision="approve", actor="a")  # now it is decided
    with pytest.raises(NotFound):
        await runner.decide("other-run", approval["id"], decision="approve", actor="a")


async def test_internal_error_after_a_winning_cancel_adds_no_event(runner, monkeypatch):
    llm = ScriptedLLM([calls(STATUS)])  # then it runs out: an internal error at the next turn
    run = await runner.create_run("Check payments-api")
    original = runner.store.finish_run

    async def cancel_then_finish(*args, **kwargs):
        await runner.store.cancel_run(run["id"], decided_by="system")  # the cancel wins the row
        return await original(*args, **kwargs)

    monkeypatch.setattr(runner.store, "finish_run", cancel_then_finish)
    assert await runner.run_segment(run["id"], llm_client=llm) == "cancelled"
    assert await events(runner, run["id"], "error") == []
    assert await events(runner, run["id"], "done") == []  # the cancel path (T3) writes its own


# --- T3: expiry and cancel -----------------------------------------------------------------------


async def test_expired_approval_rejects(runner, monkeypatch):
    monkeypatch.setattr(cfg.approval, "ttl_s", 0)
    run_id, llm, approval = await paused(runner, [incident_call(), final("No incident: the approval expired.")])
    monkeypatch.setattr(runner, "_client", lambda mode: llm)  # the spawned continue_run uses the scripted LLM
    assert await runner.sweep() == 1
    row = await runner.store.get_approval(approval["id"])
    assert (row["status"], row["decided_by"], row["reason"]) == ("expired", "system", "approval expired")
    await wait_for_status(runner, run_id, "completed")
    assert await runner.store.list_incidents() == []
    envelope = tool_results(await runner.get_state(run_id))[0]
    assert envelope["error"] == {
        "type": "rejected",
        "message": "create_incident was rejected: approval expired",
        "retryable": False,
    }
    expired = [e for e in await events(runner, run_id) if e["kind"] in ("approval", "log")]
    assert [(e["kind"], e["status"], e["attention"]) for e in expired[-2:]] == [
        ("approval", "expired", "info"),
        ("log", None, None),
    ]
    assert expired[-1]["data"] == {"actor": "system", "action": "expire_approval", "entity_id": approval["id"]}
    assert await runner.sweep() == 0  # nothing left to expire


async def test_cancel_closes_pending_approval(runner, monkeypatch):
    monkeypatch.setattr(cfg.approval, "ttl_s", 0)
    run_id, llm, approval = await paused(runner, [incident_call(), final("done")])
    assert await runner.cancel(run_id, actor="anonymous") == {"run_id": run_id, "status": "cancelled"}
    assert (await runner.store.get_run(run_id))["status"] == "cancelled"
    row = await runner.store.get_approval(approval["id"])
    assert (row["status"], row["reason"], row["decided_by"]) == ("cancelled", "run cancelled", "anonymous")
    with pytest.raises(Conflict):
        await runner.decide(run_id, approval["id"], decision="approve", actor="a")
    assert await runner.sweep() == 0  # the sweep does not resume a cancelled run
    assert await runner.continue_run(run_id, llm_client=llm) == "cancelled"
    assert await runner.store.list_incidents() == []
    done = await events(runner, run_id, "done")
    assert [(e["status"], e["attention"]) for e in done] == [("cancelled", "info")]
