"""SQLite tables from ADR 0004 and the queries the harness needs.

LangGraph's checkpointer keeps its own tables in the same file on its own
connection. This connection runs in autocommit, so a cancelled segment never
leaves a transaction open; WAL lets readers run during the one write.
"""

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

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


def now_iso(ts: float | None = None) -> str:
    """ISO-8601 UTC with milliseconds, e.g. 2026-09-27T09:00:00.123Z. Sorts correctly as text."""
    moment = datetime.fromtimestamp(ts, UTC) if ts is not None else datetime.now(UTC)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Store:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._db = conn

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
        await self._db.close()

    # --- runs -----------------------------------------------------------------

    async def create_run(self, *, id: str, objective: str, llm_mode: str, model: str, options: dict) -> dict:
        now = now_iso()
        await self._db.execute(
            "INSERT INTO runs (id, objective, status, llm_mode, model, options_json, created_at, updated_at)"
            " VALUES (?, ?, 'running', ?, ?, ?, ?, ?)",
            (id, objective, llm_mode, model, json.dumps(options), now, now),
        )
        return await self.get_run(id)

    async def get_run(self, id: str) -> dict | None:
        async with self._db.execute("SELECT * FROM runs WHERE id = ?", (id,)) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        run = dict(row)
        run["options"] = json.loads(run.pop("options_json"))
        return run

    async def update_run(self, id: str, **fields: Any) -> None:
        unknown = set(fields) - _RUN_FIELDS
        if unknown:
            raise ValueError(f"cannot update run fields: {sorted(unknown)}")
        if "options" in fields:
            fields["options_json"] = json.dumps(fields.pop("options"))
        fields["updated_at"] = now_iso()
        sets = ", ".join(f"{name} = ?" for name in fields)
        cur = await self._db.execute(f"UPDATE runs SET {sets} WHERE id = ?", (*fields.values(), id))
        if cur.rowcount == 0:
            raise LookupError(f"run {id} not found")

    # --- events ---------------------------------------------------------------

    async def insert_event(self, event: dict) -> int:
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
        async with self._db.execute(
            "SELECT * FROM events WHERE run_id = ? AND seq > ? ORDER BY seq", (run_id, after_seq)
        ) as cur:
            rows = await cur.fetchall()
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
        await self._db.execute(
            "INSERT INTO incidents (id, idempotency_key, run_id, title, description, severity, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 'open', ?) ON CONFLICT (idempotency_key) DO NOTHING",
            (f"INC-{uuid.uuid4().hex[:8].upper()}", idempotency_key, run_id, title, description, severity, now_iso()),
        )
        async with self._db.execute("SELECT * FROM incidents WHERE idempotency_key = ?", (idempotency_key,)) as cur:
            return dict(await cur.fetchone())

    async def list_incidents(self, run_id: str | None = None) -> list[dict]:
        query, args = "SELECT * FROM incidents", ()
        if run_id is not None:
            query, args = query + " WHERE run_id = ?", (run_id,)
        async with self._db.execute(query + " ORDER BY created_at, id", args) as cur:
            return [dict(row) for row in await cur.fetchall()]
