"""SQLite tables from ADR 0004 and the queries the harness needs.

LangGraph's checkpointer keeps its own tables in the same file on its own
connection. This connection runs in autocommit, so a cancelled segment never
leaves a transaction open; WAL lets readers run during the one write.

Every coroutine shares this one connection, and statements on one connection
share its transaction. So every write takes `_write`, and multi-statement
writes run in `_transaction`: `BEGIN IMMEDIATE ... COMMIT` under the lock and
shielded, so a cancelled caller still ends the transaction.

Every read is one aiosqlite job (`execute_fetchall`). A cursor read over
several jobs keeps a WAL read snapshot open between them; a commit on the
checkpointer's connection in that window makes the next write here fail at
once with "database is locked".
"""

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

import aiosqlite

from app.clock import now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id           TEXT PRIMARY KEY,
    objective    TEXT NOT NULL,
    status       TEXT NOT NULL,
    llm_mode     TEXT NOT NULL,
    model        TEXT NOT NULL,
    options_json TEXT NOT NULL,
    final        TEXT,
    error        TEXT,
    steps        INTEGER NOT NULL DEFAULT 0,
    tool_calls   INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    finished_at  TEXT
);
CREATE TABLE IF NOT EXISTS approvals (
    id            TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL REFERENCES runs(id),
    tool_call_id  TEXT NOT NULL,
    tool          TEXT NOT NULL,
    args_json     TEXT NOT NULL,
    status        TEXT NOT NULL,
    decision_json TEXT,
    reason        TEXT,
    decided_by    TEXT,
    created_at    TEXT NOT NULL,
    decided_at    TEXT,
    expires_at    TEXT NOT NULL,
    UNIQUE (run_id, tool_call_id)
);
CREATE TABLE IF NOT EXISTS events (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT,
    t_ms       INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    node       TEXT,
    tool       TEXT,
    status     TEXT,
    attention  TEXT,
    msg        TEXT,
    data_json  TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_run_seq ON events (run_id, seq);
CREATE TABLE IF NOT EXISTS evals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL REFERENCES runs(id),
    target      TEXT NOT NULL,
    metric      TEXT NOT NULL,
    value       REAL,
    judge_model TEXT,
    error       TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS evals_run ON evals (run_id);
CREATE TABLE IF NOT EXISTS eval_reports (
    id           TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL,
    models_json  TEXT NOT NULL,
    config_json  TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    rows_json    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS incidents (
    id              TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    run_id          TEXT,
    title           TEXT NOT NULL,
    description     TEXT NOT NULL,
    severity        TEXT NOT NULL,
    status          TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
"""

# Columns update_run may change. Column names never come from callers unchecked.
_RUN_FIELDS = {"status", "final", "error", "steps", "tool_calls", "finished_at", "options"}
_OPEN = ("running", "awaiting_approval", "interrupted")  # a run in one of these can still be cancelled
T = TypeVar("T")


class Store:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._db = conn
        self._write = asyncio.Lock()  # every write; reads do not lock

    @classmethod
    async def open(cls, path: str | Path) -> "Store":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(path, isolation_level=None)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.executescript(SCHEMA)
        return cls(conn)

    async def close(self) -> None:
        async with self._write:  # FIFO: transactions already queued finish first
            await self._db.close()

    async def ping(self) -> bool:
        try:
            return (await self._one("SELECT 1 AS one"))["one"] == 1
        except Exception:
            return False

    async def _all(self, sql: str, args: tuple | list = ()) -> list:
        return list(await self._db.execute_fetchall(sql, args))

    async def _one(self, sql: str, args: tuple | list = ()):
        rows = await self._db.execute_fetchall(sql, args)
        return rows[0] if rows else None

    async def _transaction(self, work: Callable[[aiosqlite.Connection], Awaitable[T]]) -> T:
        """Run `work(conn)` in one `BEGIN IMMEDIATE ... COMMIT` under the write lock.

        Shielded: a cancelled caller gets CancelledError, but the transaction still ends with COMMIT
        or ROLLBACK. A cancelled await does not stop a statement already queued on the connection's
        thread, so an unshielded transaction could stay open and lock the file. Inside `work`, use only
        `conn.execute` and `conn.execute_fetchall`: never another store method (the lock is not
        re-entrant) and never emit an event.
        """

        async def run() -> T:
            async with self._write:
                await self._db.execute("BEGIN IMMEDIATE")
                try:
                    result = await work(self._db)
                    await self._db.execute("COMMIT")  # inside the try: a failed commit is rolled back too
                except BaseException:
                    await self._db.execute("ROLLBACK")
                    raise
                return result

        task = asyncio.ensure_future(run())
        task.add_done_callback(lambda t: t.cancelled() or t.exception())  # no "never retrieved" warning
        return await asyncio.shield(task)

    # --- runs -----------------------------------------------------------------

    async def create_run(self, *, id: str, objective: str, llm_mode: str, model: str, options: dict) -> dict:
        now = now_iso()
        async with self._write:
            await self._db.execute(
                "INSERT INTO runs (id, objective, status, llm_mode, model, options_json, created_at, updated_at)"
                " VALUES (?, ?, 'running', ?, ?, ?, ?, ?)",
                (id, objective, llm_mode, model, json.dumps(options), now, now),
            )
        return await self.get_run(id)

    async def get_run(self, id: str) -> dict | None:
        row = await self._one("SELECT * FROM runs WHERE id = ?", (id,))
        if row is None:
            return None
        run = dict(row)
        run["options"] = json.loads(run.pop("options_json"))
        return run

    async def update_run(self, id: str, **fields: Any) -> None:
        if not await self._update_run(id, fields, only_from=None):
            raise LookupError(f"run {id} not found")

    async def finish_run(self, id: str, **fields: Any) -> bool:
        """Like update_run, but only while the run is `running`; true when the row changed."""
        return await self._update_run(id, fields, only_from="running")

    async def _update_run(self, id: str, fields: dict, *, only_from: str | None) -> bool:
        unknown = set(fields) - _RUN_FIELDS
        if unknown:
            raise ValueError(f"cannot update run fields: {sorted(unknown)}")
        if "options" in fields:
            fields["options_json"] = json.dumps(fields.pop("options"))
        fields["updated_at"] = now_iso()
        sets = ", ".join(f"{name} = ?" for name in fields)  # names come from the whitelist, values are parameters
        where, args = "id = ?", [id]
        if only_from is not None:
            where, args = where + " AND status = ?", [id, only_from]
        async with self._write:
            cur = await self._db.execute(f"UPDATE runs SET {sets} WHERE {where}", (*fields.values(), *args))
        return cur.rowcount > 0

    async def list_runs(self, limit: int = 20) -> list[dict]:
        """Run summaries, newest first."""
        rows = await self._all(
            "SELECT id, objective, status, llm_mode, steps, tool_calls, created_at, updated_at FROM runs"
            " ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in rows]

    async def mark_interrupted(self) -> list[str]:
        """Startup recovery: every `running` run becomes `interrupted`. Returns their ids."""

        async def work(db: aiosqlite.Connection) -> list[str]:
            rows = await db.execute_fetchall("SELECT id FROM runs WHERE status = 'running' ORDER BY created_at, rowid")
            ids = [row["id"] for row in rows]
            await db.execute(
                "UPDATE runs SET status = 'interrupted', updated_at = ? WHERE status = 'running'", (now_iso(),)
            )
            return ids

        return await self._transaction(work)

    async def resume_run(self, id: str) -> bool:
        """`interrupted` -> `running`; true when the row changed."""
        async with self._write:
            cur = await self._db.execute(
                "UPDATE runs SET status = 'running', updated_at = ? WHERE id = ? AND status = 'interrupted'",
                (now_iso(), id),
            )
        return cur.rowcount > 0

    async def cancel_run(self, id: str, *, decided_by: str) -> int | None:
        """The run becomes `cancelled` with its pending approvals, in one transaction.

        Only from running, awaiting_approval or interrupted; None otherwise. Returns how many approvals it closed.
        """

        async def work(db: aiosqlite.Connection) -> int | None:
            now = now_iso()
            placeholders = ", ".join("?" for _ in _OPEN)
            cur = await db.execute(
                "UPDATE runs SET status = 'cancelled', finished_at = ?, updated_at = ?"
                f" WHERE id = ? AND status IN ({placeholders})",
                (now, now, id, *_OPEN),
            )
            if cur.rowcount == 0:
                return None
            cur = await db.execute(
                "UPDATE approvals SET status = 'cancelled', reason = 'run cancelled', decided_by = ?, decided_at = ?"
                " WHERE run_id = ? AND status = 'pending'",
                (decided_by, now, id),
            )
            return cur.rowcount

        return await self._transaction(work)

    # --- approvals ------------------------------------------------------------

    async def pause_run(
        self, run_id: str, *, steps: int, tool_calls: int, calls: list[dict], created_at: str, expires_at: str
    ) -> list[dict] | None:
        """The run becomes `awaiting_approval` and each call `{tool_call_id, tool, args}` gets a pending row.

        One transaction, only from `running` (None otherwise). A call that already has a row keeps it,
        so a pause that repeats after a crash asks nothing twice. Returns the rows of these calls.
        """

        async def work(db: aiosqlite.Connection) -> list[dict] | None:
            cur = await db.execute(
                "UPDATE runs SET status = 'awaiting_approval', steps = ?, tool_calls = ?, updated_at = ?"
                " WHERE id = ? AND status = 'running'",
                (steps, tool_calls, now_iso(), run_id),
            )
            if cur.rowcount == 0:
                return None  # nothing was written
            for call in calls:
                await db.execute(
                    "INSERT INTO approvals (id, run_id, tool_call_id, tool, args_json, status, created_at, expires_at)"
                    " VALUES (?, ?, ?, ?, ?, 'pending', ?, ?) ON CONFLICT (run_id, tool_call_id) DO NOTHING",
                    (
                        uuid.uuid4().hex,
                        run_id,
                        call["tool_call_id"],
                        call["tool"],
                        json.dumps(call["args"]),
                        created_at,
                        expires_at,
                    ),
                )
            rows = []
            for call in calls:
                [row] = await db.execute_fetchall(
                    "SELECT * FROM approvals WHERE run_id = ? AND tool_call_id = ?", (run_id, call["tool_call_id"])
                )
                rows.append(_approval(row))
            return rows

        return await self._transaction(work)

    async def get_approval(self, id: str) -> dict | None:
        row = await self._one("SELECT * FROM approvals WHERE id = ?", (id,))
        return _approval(row) if row else None

    async def approval_for_call(self, run_id: str, tool_call_id: str) -> dict | None:
        row = await self._one("SELECT * FROM approvals WHERE run_id = ? AND tool_call_id = ?", (run_id, tool_call_id))
        return _approval(row) if row else None

    async def list_approvals(self, *, run_id: str | None = None, status: str | None = None) -> list[dict]:
        """Oldest first."""
        where, args = [], []
        if run_id is not None:
            where.append("run_id = ?")
            args.append(run_id)
        if status is not None:
            where.append("status = ?")
            args.append(status)
        query = "SELECT * FROM approvals" + (" WHERE " + " AND ".join(where) if where else "")
        return [_approval(row) for row in await self._all(query + " ORDER BY created_at, rowid", args)]

    async def decide_approval(
        self, id: str, *, status: str, decision: dict, reason: str | None, decided_by: str
    ) -> dict | None:
        """Record a decision, only on a `pending` row of an `awaiting_approval` run; the run becomes `running`.

        One transaction. None when nothing changed (already decided, expired, cancelled, or the run moved on).
        """

        async def work(db: aiosqlite.Connection) -> dict | None:
            now = now_iso()
            cur = await db.execute(
                "UPDATE approvals SET status = ?, decision_json = ?, reason = ?, decided_by = ?, decided_at = ?"
                " WHERE id = ? AND status = 'pending'"
                " AND (SELECT status FROM runs WHERE runs.id = approvals.run_id) = 'awaiting_approval'",
                (status, json.dumps(decision), reason, decided_by, now, id),
            )
            if cur.rowcount == 0:
                return None
            await db.execute(
                "UPDATE runs SET status = 'running', updated_at = ?"
                " WHERE id = (SELECT run_id FROM approvals WHERE id = ?) AND status = 'awaiting_approval'",
                (now, id),
            )
            [row] = await db.execute_fetchall("SELECT * FROM approvals WHERE id = ?", (id,))
            return _approval(row)

        return await self._transaction(work)

    async def expired_approvals(self, now: str) -> list[dict]:
        """Pending rows past `expires_at` whose run still waits, oldest expiry first."""
        rows = await self._all(
            "SELECT approvals.* FROM approvals JOIN runs ON runs.id = approvals.run_id"
            " WHERE approvals.status = 'pending' AND approvals.expires_at <= ? AND runs.status = 'awaiting_approval'"
            " ORDER BY approvals.expires_at, approvals.rowid",
            (now,),
        )
        return [_approval(row) for row in rows]

    # --- events ---------------------------------------------------------------

    async def insert_event(self, event: dict) -> int:
        async with self._write:
            cur = await self._db.execute(
                "INSERT INTO events (run_id, t_ms, kind, node, tool, status, attention, msg, data_json, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event["run_id"],
                    event["t_ms"],
                    event["kind"],
                    event.get("node"),
                    event.get("tool"),
                    event.get("status"),
                    event.get("attention"),
                    event.get("msg"),
                    None if event.get("data") is None else json.dumps(event["data"]),
                    event["created_at"],
                ),
            )
        return cur.lastrowid

    async def list_events(self, run_id: str, after_seq: int = 0) -> list[dict]:
        rows = await self._all("SELECT * FROM events WHERE run_id = ? AND seq > ? ORDER BY seq", (run_id, after_seq))
        events = []
        for row in rows:
            event = dict(row)
            data = event.pop("data_json")
            event["data"] = None if data is None else json.loads(data)
            events.append(event)
        return events

    # --- incidents (the mock external system) ----------------------------------

    async def create_incident(
        self, *, idempotency_key: str, run_id: str | None, title: str, description: str, severity: str
    ) -> dict:
        """Create an incident, or return the existing one for a known idempotency key."""
        async with self._write:
            await self._db.execute(
                "INSERT INTO incidents (id, idempotency_key, run_id, title, description, severity, status, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 'open', ?) ON CONFLICT (idempotency_key) DO NOTHING",
                (
                    f"INC-{uuid.uuid4().hex[:8].upper()}",
                    idempotency_key,
                    run_id,
                    title,
                    description,
                    severity,
                    now_iso(),
                ),
            )
        return dict(await self._one("SELECT * FROM incidents WHERE idempotency_key = ?", (idempotency_key,)))

    async def list_incidents(self, run_id: str | None = None) -> list[dict]:
        query, args = "SELECT * FROM incidents", ()
        if run_id is not None:
            query, args = query + " WHERE run_id = ?", (run_id,)
        return [dict(row) for row in await self._all(query + " ORDER BY created_at, id", args)]

    # --- per-run evaluation --------------------------------------------------------------

    async def insert_eval(
        self, run_id: str, *, target: str, metric: str, value: float | None, judge_model: str | None, error: str | None
    ) -> None:
        async with self._write:
            await self._db.execute(
                "INSERT INTO evals (run_id, target, metric, value, judge_model, error, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, target, metric, value, judge_model, error, now_iso()),
            )

    async def list_evals(self, run_id: str) -> list[dict]:
        return [dict(row) for row in await self._all("SELECT * FROM evals WHERE run_id = ? ORDER BY id", (run_id,))]

    # --- evaluation reports ---------------------------------------------------------------

    async def insert_eval_report(self, report: dict) -> None:
        async with self._write:
            await self._db.execute(
                "INSERT INTO eval_reports (id, created_at, models_json, config_json, summary_json, rows_json)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    report["id"],
                    report["created_at"],
                    json.dumps(report["models"]),
                    json.dumps(report["config"]),
                    json.dumps(report["summary"]),
                    json.dumps(report["rows"]),
                ),
            )

    async def latest_eval_report(self) -> dict | None:
        row = await self._one("SELECT * FROM eval_reports ORDER BY created_at DESC, rowid DESC LIMIT 1")
        if row is None:
            return None
        return {
            "id": row["id"],
            "created_at": row["created_at"],
            **{key: json.loads(row[f"{key}_json"]) for key in ("models", "config", "summary", "rows")},
        }


def _approval(row) -> dict:
    """An approvals row in API shape: `args` and `decision` parsed."""
    approval = dict(row)
    approval["args"] = json.loads(approval.pop("args_json"))
    decision = approval.pop("decision_json")
    approval["decision"] = None if decision is None else json.loads(decision)
    return approval
