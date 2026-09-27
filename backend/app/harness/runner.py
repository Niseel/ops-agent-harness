"""Creates runs and runs them one segment at a time (spec: Run lifecycle).

A segment goes from the start of a run until it finishes or pauses for an
approval. It runs inside `asyncio.timeout(max_run_seconds)`, so time spent
waiting for a person is never counted. The runner writes the run row and emits
the one `done` event of a finished run. Not here yet (M3): resume, decisions,
the per-run lock and background tasks.
"""

import asyncio
import logging
import uuid

import aiosqlite
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.errors import GraphRecursionError

from app.clock import now_iso
from app.config import cfg, settings
from app.eval import metrics, online
from app.harness import policy
from app.harness.loop import STATUS_BY_ERROR, RunContext, build_graph
from app.harness.policy import RunOptions
from app.harness.state import AgentState, RunStatus
from app.harness.store import Store
from app.harness.tracer import Tracer
from app.llm.fake import FakePlanner
from app.llm.openai_compat import OpenAICompatClient

log = logging.getLogger("app.runner")
eval_log = logging.getLogger("app.eval")

LLM_MODES = ("fake", "openai")
_DONE_ATTENTION = {RunStatus.COMPLETED: "success", RunStatus.CANCELLED: "info"}  # other final statuses: error


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
        """Run until the run finishes or pauses; return its status. `llm_client` replaces the run's client (tests)."""
        run = await self.store.get_run(run_id)
        if run is None:
            raise LookupError(f"run {run_id} not found")
        opts = RunOptions.model_validate(run["options"])
        config = self._config(run_id, opts)
        # M1 runs a run once, from its start. Resume and decisions (M3) continue from the checkpoint instead.
        if run["status"] != RunStatus.RUNNING or (await self.graph.aget_state(config)).values:
            raise ValueError(f"run {run_id} has already started (status {run['status']})")
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
                out = await self.graph.ainvoke(
                    _initial_state(run), config, context=ctx, durability="sync", version="v2"
                )
        except TimeoutError:
            if not deadline.expired():  # a TimeoutError from inside the graph is a bug, not the segment limit
                return await self._internal_error(run_id, opts)
            return await self._finish(run_id, opts, "max_run_seconds")
        except GraphRecursionError:
            return await self._finish(run_id, opts, "recursion_limit")
        except Exception:  # never CancelledError: a cancelled segment is not a failed run
            return await self._internal_error(run_id, opts)

        if out.interrupts:
            await self._pause(run_id, out)
            return RunStatus.AWAITING_APPROVAL
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

    async def _pause(self, run_id: str, out) -> None:
        state = out.value
        await self.store.update_run(
            run_id, status=RunStatus.AWAITING_APPROVAL, steps=state["steps"], tool_calls=state["tool_calls"]
        )
        for pending in out.interrupts:
            await self.tracer.emit(
                run_id,
                "approval",
                node="approval",
                tool=pending.value["tool"],
                status="pending",
                attention="warn",
                msg=f"{pending.value['tool']} waits for a person's approval",
                data={**pending.value, "interrupt_id": pending.id},
            )

    async def _internal_error(self, run_id: str, opts: RunOptions) -> str:
        log.exception("run failed with an unexpected error", extra={"run_id": run_id})  # traceback: log only
        await self.tracer.emit(run_id, "error", status="failed", attention="error", msg="unexpected error, see the log")
        return await self._finish(run_id, opts, "internal_error")

    async def _finish(self, run_id: str, opts: RunOptions, error: str | None, state: dict | None = None) -> str:
        """Write the run row, emit the one `done` event, then evaluate the run when its options say so."""
        if state is None:  # the graph did not return: read the last checkpoint
            state = (await self.graph.aget_state(self._config(run_id, opts))).values
        status = STATUS_BY_ERROR[error]
        steps, tool_calls = state.get("steps", 0), state.get("tool_calls", 0)
        await self.store.update_run(
            run_id,
            status=status,
            final=state.get("final"),
            error=error,
            steps=steps,
            tool_calls=tool_calls,
            finished_at=now_iso(),
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
