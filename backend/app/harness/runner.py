"""Creates runs and runs them one segment at a time (spec: Run lifecycle, Approval).

A segment goes from the start of a run, a decision or a resume until the run
finishes or pauses for an approval. It runs inside
`asyncio.timeout(max_run_seconds)`, so time spent waiting for a person is never
counted. At a pause the runner writes the approval row; `decide` records a
decision in that row, and `continue_run` resumes the graph from it, so a crash
between the two loses nothing. The runner writes the final run row and emits
the one `done` event.
"""

import asyncio
import logging
import time
import uuid

import aiosqlite
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.errors import GraphRecursionError
from langgraph.types import Command

from app.clock import now_iso
from app.config import cfg, settings
from app.eval import metrics, online
from app.harness import policy
from app.harness.loop import STATUS_BY_ERROR, RunContext, build_graph
from app.harness.policy import RunOptions
from app.harness.state import AgentState, RunStatus
from app.harness.store import Store
from app.harness.tool_gateway import check_input
from app.harness.tracer import Tracer
from app.llm.fake import FakePlanner
from app.llm.openai_compat import OpenAICompatClient
from app.tools.registry import TOOLS

log = logging.getLogger("app.runner")
eval_log = logging.getLogger("app.eval")

LLM_MODES = ("fake", "openai")
_DONE_ATTENTION = {RunStatus.COMPLETED: "success", RunStatus.CANCELLED: "info"}  # other final statuses: error
_APPROVAL_STATUS = {"approve": "approved", "reject": "rejected", "edit": "edited"}


class NotFound(LookupError):
    """No such run or approval (404)."""


class Conflict(Exception):
    """The run or the approval is not in a state that allows this (409)."""


def resume_value(approval: dict) -> dict:
    """What the approval node's interrupt() returns for a decided row; the tools node reads it."""
    status = approval["status"]
    if status == "approved":
        return {"decision": "approve"}
    if status == "edited":
        return {"decision": "edit", "args": approval["decision"]}
    if status == "rejected":
        return {"decision": "reject", "reason": approval["reason"]}
    if status == "expired":
        return {"decision": "reject", "reason": "approval expired"}
    raise ValueError(f"approval {approval['id']} is {status}: there is no decision to resume with")


class Runner:
    def __init__(self, store: Store, saver_conn: aiosqlite.Connection, saver: AsyncSqliteSaver) -> None:
        self.store = store
        self.tracer = Tracer(store, settings.secrets())
        self.graph = build_graph(saver)
        self._saver_conn = saver_conn
        self._openai: OpenAICompatClient | None = None  # one per process, made on first use

    @classmethod
    async def open(cls, db_path) -> "Runner":
        store = await Store.open(db_path)
        # The checkpointer gets its own connection to the same file (WAL, see store.py). Autocommit, like the
        # store: a segment cancelled between the saver's INSERT and its commit would otherwise keep a write
        # transaction open, and every later write to the file would fail with "database is locked".
        conn = await aiosqlite.connect(db_path, isolation_level=None)
        try:
            # allowed_msgpack_modules=None: load only safe types from a checkpoint, never arbitrary classes.
            saver = AsyncSqliteSaver(conn, serde=JsonPlusSerializer(allowed_msgpack_modules=None))
            await saver.setup()
        except BaseException:
            await conn.close()
            await store.close()
            raise
        return cls(store, conn, saver)

    async def close(self) -> None:
        await self._saver_conn.close()
        await self.store.close()

    async def create_run(self, objective: str, *, llm: str | None = None, options: dict | None = None) -> dict:
        """Store a new run with its effective options. Nothing runs yet. Bad input raises ValueError."""
        if not objective.strip() or len(objective) > 2000:
            raise ValueError("objective must be 1 to 2000 characters")
        mode = llm or settings.llm_default
        if mode not in LLM_MODES:
            raise ValueError(f"llm must be one of {LLM_MODES}")
        opts = policy.parse_options(options, allow_faults=settings.allow_fault_injection)
        if opts.evaluate is None:
            # Online evaluation by default only when a judge answers; the judge is asked only when that default is on.
            opts.evaluate = cfg.eval.online_default and await metrics.get_judge().reachable()
        return await self.store.create_run(
            id=uuid.uuid4().hex,
            objective=objective,
            llm_mode=mode,
            model="fake" if mode == "fake" else settings.llm_model,
            options=opts.model_dump(mode="json"),
        )

    async def run_segment(self, run_id: str, *, llm_client=None) -> str:
        """The first segment: from the start until the run finishes or pauses; return its status.

        `llm_client` replaces the run's client (tests). Every later segment goes through `continue_run`.
        """
        run = await self.store.get_run(run_id)
        if run is None:
            raise NotFound(f"run {run_id} not found")
        config = self._config(run_id, RunOptions.model_validate(run["options"]))
        if run["status"] != RunStatus.RUNNING or (await self.graph.aget_state(config)).values:
            raise ValueError(f"run {run_id} has already started (status {run['status']})")
        return await self._segment(run, _initial_state(run), llm_client)

    async def continue_run(self, run_id: str, *, llm_client=None) -> str:
        """Every later segment: after a decision, an expiry or a resume. Returns the run's status."""
        run = await self.store.get_run(run_id)
        if run is None:
            raise NotFound(f"run {run_id} not found")
        if run["status"] != RunStatus.RUNNING:
            return run["status"]  # e.g. a cancel won
        snap = await self.graph.aget_state(self._config(run_id, RunOptions.model_validate(run["options"])))
        if not snap.values:  # the process died before the first step
            graph_input = _initial_state(run)
        elif snap.interrupts:
            row = await self.store.approval_for_call(run_id, snap.interrupts[0].value["tool_call_id"])
            if row is None or row["status"] == "pending":
                # The process died between the checkpoint and the approval row: ask again, run nothing.
                return await self._pause(run_id, snap.values, snap.interrupts)
            graph_input = Command(resume=resume_value(row))  # rebuilt from the row, so a crash loses no decision
        else:
            graph_input = None  # LangGraph continues after the last saved step
        return await self._segment(run, graph_input, llm_client)

    async def decide(
        self,
        run_id: str,
        approval_id: str,
        *,
        decision: str,
        reason: str | None = None,
        args: dict | None = None,
        actor: str,
    ) -> dict:
        """Record a person's decision. Checks in order: NotFound (404), Conflict (409), ValueError (422).

        It does not run the graph: the caller then runs `continue_run` (the CLI and tests await it, the API spawns it).
        """
        approval = await self.store.get_approval(approval_id)
        if approval is None or approval["run_id"] != run_id:
            raise NotFound(f"approval {approval_id} not found")
        if approval["status"] != "pending":
            raise Conflict(f"approval {approval_id} is {approval['status']}")
        run = await self.store.get_run(run_id)
        if run["status"] != RunStatus.AWAITING_APPROVAL:
            raise Conflict(f"run {run_id} is {run['status']}")
        if decision not in _APPROVAL_STATUS:
            raise ValueError(f"decision must be one of {sorted(_APPROVAL_STATUS)}")
        if decision == "reject" and not (reason or "").strip():
            raise ValueError("a rejection needs a reason")
        if reason is not None and len(reason) > 500:  # the API's limit; the CLI calls decide directly
            raise ValueError("reason must be at most 500 characters")
        stored = None
        if decision == "edit":
            model, refusal = check_input(TOOLS[approval["tool"]], args)
            if refusal is not None:
                raise ValueError(refusal["error"]["message"])  # the row stays pending
            stored = model.model_dump(mode="json")
        row = await self.store.decide_approval(
            approval_id, status=_APPROVAL_STATUS[decision], decision=stored, reason=reason, decided_by=actor
        )
        if row is None:
            raise Conflict(f"approval {approval_id} was decided or cancelled meanwhile")
        await self._decision_event(run_id, row)
        await self.audit(run_id, actor, "decide_approval", approval_id)
        return row

    async def audit(self, run_id: str | None, actor: str, action: str, entity_id: str) -> None:
        """A `log` event for a state-changing action (spec: Events and logs)."""
        await self.tracer.emit(
            run_id,
            "log",
            msg=f"{actor} {action} {entity_id}",
            data={"actor": actor, "action": action, "entity_id": entity_id},
        )

    async def _segment(self, run: dict, graph_input, llm_client) -> str:
        """Invoke the graph from `graph_input` until it finishes or pauses."""
        run_id = run["id"]
        opts = RunOptions.model_validate(run["options"])
        config = self._config(run_id, opts)
        ctx = RunContext(
            run_id=run_id,
            limits=opts.limits,
            faults=opts.faults,
            llm=llm_client or self._client(run["llm_mode"]),
            store=self.store,
            tracer=self.tracer,
        )
        deadline = asyncio.timeout(opts.limits.max_run_seconds)
        try:
            async with deadline:
                # The context is not checkpointed: every invoke gets a fresh one, resumes included.
                out = await self.graph.ainvoke(graph_input, config, context=ctx, durability="sync", version="v2")
        except TimeoutError:
            if not deadline.expired():  # a TimeoutError from inside the graph is a bug, not the segment limit
                return await self._internal_error(run_id, opts)
            return await self._finish(run_id, opts, "max_run_seconds")
        except GraphRecursionError:
            return await self._finish(run_id, opts, "recursion_limit")
        except Exception:  # never CancelledError: a cancelled segment is not a failed run
            return await self._internal_error(run_id, opts)

        if out.interrupts:
            return await self._pause(run_id, out.value, out.interrupts)
        return await self._finish(run_id, opts, out.value["error"], out.value)

    async def get_state(self, run_id: str) -> dict:
        """The run's last checkpoint (empty before the first step)."""
        return (await self.graph.aget_state({"configurable": {"thread_id": run_id}})).values

    def _client(self, mode: str):
        if mode == "fake":
            return FakePlanner()
        if self._openai is None:
            self._openai = OpenAICompatClient.from_settings()
        return self._openai

    @staticmethod
    def _config(run_id: str, opts: RunOptions) -> dict:
        # Through the module, so a test can patch policy.recursion_limit.
        return {"configurable": {"thread_id": run_id}, "recursion_limit": policy.recursion_limit(opts.limits)}

    async def _pause(self, run_id: str, values: dict, interrupts) -> str:
        """Write the approval rows and the pause in one transaction, then one `approval` event per new row."""
        # A call asked before (a pause repeated after a crash) keeps its row and gets no second event.
        asked = set()
        for pending in interrupts:
            if await self.store.approval_for_call(run_id, pending.value["tool_call_id"]) is not None:
                asked.add(pending.value["tool_call_id"])
        now = time.time()
        rows = await self.store.pause_run(
            run_id,
            steps=values["steps"],
            tool_calls=values["tool_calls"],
            calls=[pending.value for pending in interrupts],
            created_at=now_iso(now),
            expires_at=now_iso(now + cfg.approval.ttl_s),
        )
        if rows is None:  # the run is no longer running (a cancel won): nothing to ask
            return (await self.store.get_run(run_id))["status"]
        for pending, row in zip(interrupts, rows, strict=True):
            if row["tool_call_id"] in asked:
                continue
            await self.tracer.emit(
                run_id,
                "approval",
                node="approval",
                tool=row["tool"],
                status="pending",
                attention="warn",
                msg=f"{row['tool']} waits for a person's approval",
                data={
                    **pending.value,
                    "interrupt_id": pending.id,
                    "approval_id": row["id"],
                    "expires_at": row["expires_at"],
                    # max_tool_calls when a call blocked in the same reply ends the run after this one
                    "run_error": values.get("error"),
                },
            )
        return RunStatus.AWAITING_APPROVAL

    async def _decision_event(self, run_id: str, row: dict) -> None:
        tool, status, by = row["tool"], row["status"], row["decided_by"]
        msg = {
            "approved": f"{tool} approved by {by}",
            "rejected": f"{tool} rejected by {by}: {row['reason']}",
            "edited": f"{tool} edited by {by}",
            "expired": f"{tool} approval expired",
        }[status]
        await self.tracer.emit(
            run_id,
            "approval",
            node="approval",
            tool=tool,
            status=status,
            attention=None if status == "approved" else "info",
            msg=msg,
            data={
                "approval_id": row["id"],
                "tool_call_id": row["tool_call_id"],
                "decision": resume_value(row),
                "decided_by": by,
            },
        )

    async def _internal_error(self, run_id: str, opts: RunOptions) -> str:
        log.exception("run failed with an unexpected error", extra={"run_id": run_id})  # traceback: log only
        return await self._finish(run_id, opts, "internal_error")

    async def _finish(self, run_id: str, opts: RunOptions, error: str | None, state: dict | None = None) -> str:
        """Write the run row, emit the one `done` event, then evaluate the run when its options say so."""
        if state is None:  # the graph did not return: read the last checkpoint
            state = (await self.graph.aget_state(self._config(run_id, opts))).values
        status = STATUS_BY_ERROR[error]
        steps, tool_calls = state.get("steps", 0), state.get("tool_calls", 0)
        finished = await self.store.finish_run(
            run_id,
            status=status,
            final=state.get("final"),
            error=error,
            steps=steps,
            tool_calls=tool_calls,
            finished_at=now_iso(),
        )
        if not finished:  # a cancel won: it wrote the final row and the one `done`
            return (await self.store.get_run(run_id))["status"]
        if error == "internal_error":
            # Only once this segment owns the final row, so nothing lands after a cancel's `done`.
            await self.tracer.emit(
                run_id, "error", status="failed", attention="error", msg="unexpected error, see the log"
            )
        await self.tracer.emit(
            run_id,
            "done",
            status=status,
            attention=_DONE_ATTENTION.get(status, "error"),
            msg=f"run {status}" + (f" ({error})" if error else ""),
            data={"status": status, "error": error, "steps": steps, "tool_calls": tool_calls},
        )
        if opts.evaluate:
            # After `done` and outside the segment's time limit. Scores never change the run.
            try:
                await online.evaluate_run(
                    run_id, state, judge=metrics.get_judge(), store=self.store, tracer=self.tracer
                )
            except Exception:  # CancelledError is not caught
                eval_log.exception("online evaluation failed", extra={"run_id": run_id})
        return status


def _initial_state(run: dict) -> AgentState:
    return AgentState(
        run_id=run["id"],
        objective=run["objective"],
        messages=[{"role": "user", "content": run["objective"]}],
        pending=[],
        decisions={},
        steps=0,
        tool_calls=0,
        tool_attempts={},
        llm_attempts=0,
        embed_attempts=0,
        call_counts={},
        repairs=0,
        incidents=0,
        status=RunStatus.RUNNING.value,  # plain str: the checkpoint loads only safe types
        final=None,
        error=None,
    )
