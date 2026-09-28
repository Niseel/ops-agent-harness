import asyncio
import sqlite3

import aiosqlite
import pytest

from app.harness.store import Store


async def test_run_row_round_trip(tmp_path):
    store = await Store.open(tmp_path / "harness.db")
    try:
        run = await store.create_run(
            id="r1",
            objective="check payments-api",
            llm_mode="fake",
            model="fake",
            options={"limits": {"max_steps": 8}, "faults": {}, "evaluate": None},
        )
        assert (run["status"], run["steps"], run["options"]["limits"]) == ("running", 0, {"max_steps": 8})
        await store.update_run("r1", status="completed", final="done", steps=3, finished_at="2026-09-27T09:00:00.000Z")
        with pytest.raises(ValueError):
            await store.update_run("r1", objective="changed")
        # A column name is never taken from a caller unchecked, even one shaped like SQL injection.
        with pytest.raises(ValueError):
            await store.update_run("r1", **{"status; DROP TABLE runs;--": "completed"})
    finally:
        await store.close()
    reopened = await Store.open(tmp_path / "harness.db")  # state lives in the file
    try:
        run = await reopened.get_run("r1")
        assert (run["status"], run["final"], run["steps"]) == ("completed", "done", 3)
        assert run["updated_at"] >= run["created_at"]
        assert await reopened.get_run("missing") is None
    finally:
        await reopened.close()


async def test_incident_insert_is_idempotent(store):
    first = await store.create_incident(
        idempotency_key="r1:c1",
        run_id="r1",
        title="payments-api is degraded",
        description="5xx errors",
        severity="SEV2",
    )
    again = await store.create_incident(
        idempotency_key="r1:c1", run_id="r1", title="other title", description="other", severity="SEV1"
    )
    assert again == first and first["id"].startswith("INC-") and first["status"] == "open"
    await store.create_incident(
        idempotency_key="r2:c1", run_id="r2", title="orders-db is down", description="down", severity="SEV1"
    )
    assert len(await store.list_incidents()) == 2
    assert [i["id"] for i in await store.list_incidents("r1")] == [first["id"]]


async def test_list_events_after_seq_and_none_data_round_trip(store):
    seq1 = await store.insert_event(
        {"run_id": "r1", "t_ms": 1, "kind": "stage", "created_at": "2026-09-27T09:00:00.000Z", "data": None}
    )
    seq2 = await store.insert_event(
        {"run_id": "r1", "t_ms": 2, "kind": "stage", "created_at": "2026-09-27T09:00:00.001Z", "data": {"n": 1}}
    )
    all_events = await store.list_events("r1")
    assert [e["seq"] for e in all_events] == [seq1, seq2]
    assert all_events[0]["data"] is None  # None round-trips as None, not "null" or {}
    assert all_events[1]["data"] == {"n": 1}
    assert await store.list_events("r1", after_seq=seq2) == []  # nothing past the last seq
    assert await store.list_events("r1", after_seq=10_000) == []  # a seq far beyond any event


async def test_audit_event_has_null_run_id(store):
    """Audit events for state-changing API calls use run_id = None (spec: Events and logs)."""
    seq = await store.insert_event(
        {"run_id": None, "t_ms": 1, "kind": "log", "created_at": "2026-09-27T09:00:00.000Z", "data": {"actor": "x"}}
    )
    async with store._db.execute("SELECT run_id, kind, data_json FROM events WHERE seq = ?", (seq,)) as cur:
        row = await cur.fetchone()
    assert (row["run_id"], row["kind"]) == (None, "log")
    # SQL's "NULL = NULL" is unknown, not true, so a run-scoped list never picks up an audit event
    # by passing None as the run_id, nor does it leak into another run's list.
    assert await store.list_events(None) == []
    assert await store.list_events("r1") == []


async def test_schema_has_all_six_adr_tables(store):
    async with store._db.execute("SELECT name FROM sqlite_master WHERE type = 'table'") as cur:
        names = {row["name"] for row in await cur.fetchall()}
    assert {"runs", "approvals", "events", "evals", "eval_reports", "incidents"} <= names


async def test_runs_table_has_adr_0004_columns(store):
    async with store._db.execute("PRAGMA table_info(runs)") as cur:
        cols = {row["name"] for row in await cur.fetchall()}
    assert cols == {
        "id",
        "objective",
        "status",
        "llm_mode",
        "model",
        "options_json",
        "final",
        "error",
        "steps",
        "tool_calls",
        "created_at",
        "updated_at",
        "finished_at",
    }


async def test_approvals_table_has_adr_0004_columns_and_unique_constraint(store):
    async with store._db.execute("PRAGMA table_info(approvals)") as cur:
        cols = {row["name"] for row in await cur.fetchall()}
    assert cols == {
        "id",
        "run_id",
        "tool_call_id",
        "tool",
        "args_json",
        "status",
        "decision_json",
        "reason",
        "decided_by",
        "created_at",
        "decided_at",
        "expires_at",
    }
    await store.create_run(
        id="r1", objective="x", llm_mode="fake", model="fake", options={"limits": {}, "faults": {}, "evaluate": None}
    )
    row = {
        "id": "a1",
        "run_id": "r1",
        "tool_call_id": "c1",
        "tool": "create_incident",
        "args_json": "{}",
        "status": "pending",
        "created_at": "2026-09-27T09:00:00.000Z",
        "expires_at": "2026-09-27T09:15:00.000Z",
    }
    insert_sql = (
        "INSERT INTO approvals (id, run_id, tool_call_id, tool, args_json, status, created_at, expires_at)"
        " VALUES (:id, :run_id, :tool_call_id, :tool, :args_json, :status, :created_at, :expires_at)"
    )
    await store._db.execute(insert_sql, row)
    with pytest.raises(sqlite3.IntegrityError):
        await store._db.execute(insert_sql, {**row, "id": "a2"})  # same (run_id, tool_call_id)


async def test_update_unknown_run_raises(store):
    with pytest.raises(LookupError):
        await store.update_run("missing", status="completed")


async def test_latest_eval_report_round_trip(store):
    assert await store.latest_eval_report() is None
    old = {"id": "a", "created_at": "2026-09-27T09:00:00.000Z", "models": {}, "config": {}, "summary": {}, "rows": []}
    new = {**old, "id": "b", "created_at": "2026-09-27T10:00:00.000Z", "rows": [{"mode": "hybrid", "hit@3": 1.0}]}
    same_time = {**new, "id": "c"}
    await store.insert_eval_report(old)
    await store.insert_eval_report(new)
    assert await store.latest_eval_report() == new
    await store.insert_eval_report(same_time)
    assert (await store.latest_eval_report())["id"] == "c"  # same created_at: the last one inserted


async def test_evals_listed_per_run(store):
    await store.create_run(id="r1", objective="o", llm_mode="fake", model="fake", options={})
    await store.create_run(id="r2", objective="o", llm_mode="fake", model="fake", options={})
    await store.insert_eval(
        "r1", target="search:c0", metric="context_relevance", value=0.8, judge_model="j", error=None
    )
    await store.insert_eval(
        "r2", target="answer", metric="faithfulness", value=None, judge_model="j", error="judge unreachable"
    )
    await store.insert_eval("r1", target="answer", metric="faithfulness", value=0.5, judge_model="j", error=None)
    rows = await store.list_evals("r1")
    assert [(r["target"], r["metric"], r["value"]) for r in rows] == [
        ("search:c0", "context_relevance", 0.8),
        ("answer", "faithfulness", 0.5),
    ]
    assert rows[0]["created_at"].endswith("Z") and rows[0]["judge_model"] == "j"
    assert (await store.list_evals("r2"))[0]["error"] == "judge unreachable"
    assert await store.list_evals("none") == []


# --- M3 T1: run lifecycle and approvals ------------------------------------------------------

CALL = {"tool_call_id": "c1", "tool": "create_incident", "args": {"title": "orders-db is down", "severity": "SEV1"}}
CREATED, EXPIRES = "2026-09-28T09:00:00.000Z", "2026-09-28T09:15:00.000Z"


async def new_run(store, id="r1", status=None):
    await store.create_run(id=id, objective="o", llm_mode="fake", model="fake", options={})
    if status:
        await store.update_run(id, status=status)


async def pause(store, run_id="r1", calls=(CALL,)):
    return await store.pause_run(
        run_id, steps=2, tool_calls=1, calls=list(calls), created_at=CREATED, expires_at=EXPIRES
    )


async def test_pause_run_writes_row_and_status_together(store):
    await new_run(store)
    [row] = await pause(store)
    run = await store.get_run("r1")
    assert (run["status"], run["steps"], run["tool_calls"]) == ("awaiting_approval", 2, 1)
    assert row["run_id"] == "r1" and row["tool_call_id"] == "c1" and row["tool"] == "create_incident"
    assert row["args"] == CALL["args"] and row["status"] == "pending" and row["decision"] is None
    assert (row["created_at"], row["expires_at"], row["decided_at"]) == (CREATED, EXPIRES, None)
    assert await store.get_approval(row["id"]) == row == await store.approval_for_call("r1", "c1")

    assert await pause(store) is None  # the run is no longer running: nothing written
    assert len(await store.list_approvals(run_id="r1")) == 1

    await store.update_run("r1", status="running")  # e.g. a resume after a crash repeats the pause
    [again] = await pause(store)
    assert again["id"] == row["id"]  # the same call keeps its row
    assert await store.get_approval("nope") is None and await store.approval_for_call("r1", "nope") is None


async def test_decide_approval_is_conditional(store):
    await new_run(store)
    [row] = await pause(store)
    decided = await store.decide_approval(
        row["id"], status="approved", decision={"decision": "approve"}, reason=None, decided_by="anonymous"
    )
    assert decided["status"] == "approved" and decided["decision"] == {"decision": "approve"}
    assert decided["decided_by"] == "anonymous" and decided["decided_at"].endswith("Z")
    assert (await store.get_run("r1"))["status"] == "running"
    # A second decision on the same row changes nothing.
    assert await store.decide_approval(row["id"], status="rejected", decision={}, reason="no", decided_by="x") is None

    # A pending row whose run is not awaiting approval (e.g. it was interrupted) cannot be decided.
    await new_run(store, "r2")
    [row2] = await pause(store, "r2")
    await store.update_run("r2", status="interrupted")
    assert await store.decide_approval(row2["id"], status="approved", decision={}, reason=None, decided_by="x") is None
    assert (await store.get_approval(row2["id"]))["status"] == "pending"

    # A cancelled run's approval cannot be decided.
    await new_run(store, "r3")
    [row3] = await pause(store, "r3")
    await store.cancel_run("r3", decided_by="anonymous")
    assert await store.decide_approval(row3["id"], status="approved", decision={}, reason=None, decided_by="x") is None
    assert await store.decide_approval("nope", status="approved", decision={}, reason=None, decided_by="x") is None


async def test_cancel_run_closes_pending_approvals(store):
    await new_run(store)
    other = {**CALL, "tool_call_id": "c2"}
    first, second = await pause(store, calls=(CALL, other))
    await store.decide_approval(first["id"], status="approved", decision={}, reason=None, decided_by="a")
    await store.update_run("r1", status="awaiting_approval")
    assert await store.cancel_run("r1", decided_by="anonymous") == 1  # only the pending one
    run = await store.get_run("r1")
    assert run["status"] == "cancelled" and run["finished_at"]
    closed = await store.get_approval(second["id"])
    assert (closed["status"], closed["reason"], closed["decided_by"]) == ("cancelled", "run cancelled", "anonymous")
    assert (await store.get_approval(first["id"]))["status"] == "approved"
    assert await store.cancel_run("r1", decided_by="anonymous") is None  # already final
    for status in ("running", "interrupted"):
        await new_run(store, f"r-{status}", status)
        assert await store.cancel_run(f"r-{status}", decided_by="x") == 0
    await new_run(store, "done", "completed")
    assert await store.cancel_run("done", decided_by="x") is None
    assert await store.cancel_run("nope", decided_by="x") is None


async def test_expired_approvals_only_for_awaiting_runs(store):
    await new_run(store, "r1")
    await new_run(store, "r2")
    await new_run(store, "r3")
    await store.pause_run(
        "r1", steps=1, tool_calls=0, calls=[CALL], created_at=CREATED, expires_at="2026-09-28T09:02:00.000Z"
    )
    await store.pause_run(
        "r2", steps=1, tool_calls=0, calls=[CALL], created_at=CREATED, expires_at="2026-09-28T09:01:00.000Z"
    )
    await store.pause_run(
        "r3", steps=1, tool_calls=0, calls=[CALL], created_at=CREATED, expires_at="2026-09-28T09:30:00.000Z"
    )
    await store.cancel_run("r2", decided_by="x")  # cancelled: its row is no longer pending
    await new_run(store, "r4")
    [r4] = await pause(store, "r4")
    await store.update_run("r4", status="interrupted")  # pending row, but the run does not wait
    now = "2026-09-28T09:20:00.000Z"
    assert [a["run_id"] for a in await store.expired_approvals(now)] == ["r1"]
    assert [a["run_id"] for a in await store.expired_approvals("2026-09-28T09:30:00.000Z")] == ["r1", "r3"]
    assert await store.expired_approvals("2026-09-28T08:00:00.000Z") == []


async def test_mark_interrupted_and_resume_run(store):
    await new_run(store, "a")
    await new_run(store, "b", "awaiting_approval")
    await new_run(store, "c")
    assert await store.mark_interrupted() == ["a", "c"]
    assert [(await store.get_run(i))["status"] for i in "abc"] == ["interrupted", "awaiting_approval", "interrupted"]
    assert await store.mark_interrupted() == []
    assert await store.resume_run("a") is True and (await store.get_run("a"))["status"] == "running"
    assert await store.resume_run("a") is False  # not interrupted any more
    assert await store.resume_run("b") is False and await store.resume_run("nope") is False


async def test_finish_run_only_from_running(store):
    await new_run(store)
    assert await store.finish_run("r1", status="completed", final="done", finished_at=CREATED) is True
    assert (await store.get_run("r1"))["final"] == "done"
    assert await store.finish_run("r1", status="failed", error="internal_error") is False  # already final
    assert (await store.get_run("r1"))["status"] == "completed"
    assert await store.finish_run("nope", status="completed") is False
    with pytest.raises(ValueError):
        await store.finish_run("r1", objective="x")


async def test_list_runs_newest_first(store):
    for i in range(3):
        await new_run(store, f"r{i}")
    runs = await store.list_runs()
    assert [r["id"] for r in runs] == ["r2", "r1", "r0"]  # same created_at: the last inserted first
    assert runs[0].keys() == {
        "id",
        "objective",
        "status",
        "llm_mode",
        "steps",
        "tool_calls",
        "created_at",
        "updated_at",
    }
    assert [r["id"] for r in await store.list_runs(limit=2)] == ["r2", "r1"]


async def test_list_approvals_filters(store):
    await new_run(store, "r1")
    await new_run(store, "r2")
    [a1] = await pause(store, "r1")
    [a2] = await pause(store, "r2")
    await store.decide_approval(
        a1["id"], status="rejected", decision={"decision": "reject"}, reason="no", decided_by="x"
    )
    assert [a["id"] for a in await store.list_approvals()] == [a1["id"], a2["id"]]
    assert [a["id"] for a in await store.list_approvals(status="pending")] == [a2["id"]]
    assert [a["id"] for a in await store.list_approvals(run_id="r1")] == [a1["id"]]
    assert await store.list_approvals(run_id="r1", status="pending") == []


async def test_cancelled_transaction_leaves_no_open_transaction(store):
    await new_run(store)
    inside, release = asyncio.Event(), asyncio.Event()

    async def slow(db):
        await db.execute("UPDATE runs SET steps = 7 WHERE id = 'r1'")
        inside.set()
        await release.wait()
        return "done"

    caller = asyncio.ensure_future(store._transaction(slow))
    await inside.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    release.set()
    async with asyncio.timeout(1):  # "database is locked" would wait the 5 s busy timeout
        await store.update_run("r1", status="completed")
    assert not store._db.in_transaction
    assert (await store.get_run("r1"))["steps"] == 7  # the shielded transaction still committed


async def test_writes_wait_for_an_open_transaction(store):
    await new_run(store)
    inside, release = asyncio.Event(), asyncio.Event()

    async def failing(db):
        await db.execute("UPDATE runs SET steps = 9 WHERE id = 'r1'")
        inside.set()
        await release.wait()
        raise RuntimeError("boom")

    transaction = asyncio.ensure_future(store._transaction(failing))
    await inside.wait()
    write = asyncio.ensure_future(store.insert_event({"run_id": "r1", "t_ms": 1, "kind": "log", "created_at": CREATED}))
    await asyncio.sleep(0.02)
    assert not write.done()  # it waits for the lock instead of joining the open transaction
    release.set()
    with pytest.raises(RuntimeError):
        await transaction
    await write
    assert (await store.get_run("r1"))["steps"] == 0  # rolled back
    assert [e["kind"] for e in await store.list_events("r1")] == ["log"]  # not rolled back with it
    assert not store._db.in_transaction


async def test_ping(store):
    assert await store.ping() is True
    await store.close()
    assert await store.ping() is False


async def test_pause_run_returns_rows_in_call_order(store):
    await new_run(store)
    other = {**CALL, "tool_call_id": "c2", "tool": "get_status"}
    rows = await pause(store, calls=(CALL, other))
    assert [r["tool_call_id"] for r in rows] == ["c1", "c2"]


async def test_pause_run_bad_args_rolls_back_status_too(store):
    """A call whose args cannot be JSON-encoded fails json.dumps inside the transaction; the run's
    move to `awaiting_approval` rolls back with it, and no approval row is left behind."""
    await new_run(store)
    bad = {**CALL, "args": {"set": {1, 2}}}  # a set has no JSON form
    with pytest.raises(TypeError):
        await pause(store, calls=(bad,))
    run = await store.get_run("r1")
    assert run["status"] == "running"
    assert await store.list_approvals(run_id="r1") == []
    assert not store._db.in_transaction


async def test_cancel_run_on_interrupted_run_closes_pending_approval(store):
    await new_run(store)
    [row] = await pause(store)
    await store.update_run("r1", status="interrupted")  # e.g. the process died while it waited
    assert await store.cancel_run("r1", decided_by="anonymous") == 1
    run = await store.get_run("r1")
    assert run["status"] == "cancelled" and run["finished_at"]
    closed = await store.get_approval(row["id"])
    assert (closed["status"], closed["reason"], closed["decided_by"]) == ("cancelled", "run cancelled", "anonymous")


async def test_expired_approvals_includes_exact_expiry_boundary(store):
    await new_run(store)
    await pause(store)  # expires_at = EXPIRES
    assert [a["run_id"] for a in await store.expired_approvals(EXPIRES)] == ["r1"]  # <=, not <


async def test_decide_approval_moves_run_to_running_only_once(store):
    await new_run(store)
    [row] = await pause(store)
    await store.decide_approval(
        row["id"], status="approved", decision={"decision": "approve"}, reason=None, decided_by="anonymous"
    )
    assert (await store.get_run("r1"))["status"] == "running"
    await store.update_run("r1", status="awaiting_approval")  # e.g. a second pause right after
    # A second decision on the already-decided row must not flip the run back to running.
    assert await store.decide_approval(row["id"], status="approved", decision={}, reason=None, decided_by="x") is None
    assert (await store.get_run("r1"))["status"] == "awaiting_approval"


async def test_list_approvals_parses_args_and_decision(store):
    await new_run(store)
    [row] = await pause(store)
    await store.decide_approval(
        row["id"],
        status="edited",
        decision={"decision": "edit", "args": {"severity": "SEV2"}},
        reason=None,
        decided_by="x",
    )
    [listed] = await store.list_approvals(run_id="r1")
    assert listed["args"] == CALL["args"]  # a dict, not a JSON string
    assert listed["decision"] == {"decision": "edit", "args": {"severity": "SEV2"}}


async def test_reads_do_not_break_writes_from_another_connection(store, tmp_path):
    # The checkpointer writes on its own connection. A read that kept a cursor open across aiosqlite jobs
    # held a WAL snapshot, and a commit there then made the next write here fail with "database is locked".
    await new_run(store)
    other = await aiosqlite.connect(tmp_path / "harness.db", isolation_level=None)
    await other.execute("CREATE TABLE IF NOT EXISTS other_writes (i INTEGER)")
    failures = []

    async def reads():
        for _ in range(300):
            await store.get_run("r1")
            await store.list_events("r1")

    async def other_connection():
        for i in range(300):
            await other.execute("INSERT INTO other_writes VALUES (?)", (i,))

    async def writes():
        for i in range(300):
            try:
                await store.insert_event({"run_id": "r1", "t_ms": i, "kind": "log", "created_at": CREATED})
            except Exception as exc:
                failures.append(repr(exc))

    try:
        await asyncio.gather(reads(), reads(), other_connection(), writes())
    finally:
        await other.close()
    assert failures == []


async def test_close_waits_for_a_transaction_in_flight(store, tmp_path):
    await new_run(store)
    inside, release = asyncio.Event(), asyncio.Event()

    async def slow(db):
        await db.execute("UPDATE runs SET steps = 5 WHERE id = 'r1'")
        inside.set()
        await release.wait()

    transaction = asyncio.ensure_future(store._transaction(slow))
    await inside.wait()
    closing = asyncio.ensure_future(store.close())
    await asyncio.sleep(0.02)
    assert not closing.done()  # it waits for the write lock
    release.set()
    await transaction
    await closing
    reopened = await Store.open(tmp_path / "harness.db")  # the store fixture's file
    try:
        assert (await reopened.get_run("r1"))["steps"] == 5  # committed before the close
    finally:
        await reopened.close()
