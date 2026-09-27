import sqlite3

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
