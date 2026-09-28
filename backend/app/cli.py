"""Command line for the harness (spec: CLI). Same database and runner methods as the API, but no recovery,
no expiry sweep and no background tasks: every segment is awaited here.

    uv run python -m app.cli run "payments-api is slow" --llm fake
"""

import argparse
import asyncio
import json
import logging
import sys
import threading
from collections.abc import Awaitable, Callable
from functools import partial

from pydantic import ValidationError

from app import log
from app.auth import current_user
from app.config import cfg, settings
from app.eval import golden, metrics
from app.harness.runner import Conflict, NotFound, Runner
from app.harness.state import RunStatus
from app.harness.tool_gateway import describe
from app.kb import qdrant
from app.kb.ingest import ingest


def _json_object(text: str) -> dict:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"not valid JSON: {exc}") from None
    if not isinstance(value, dict):
        raise argparse.ArgumentTypeError("must be a JSON object")
    return value


def _limit(text: str) -> int:
    value = int(text)
    if not 1 <= value <= 100:
        raise argparse.ArgumentTypeError("must be 1 to 100")
    return value


def _modes(text: str) -> tuple[str, ...]:
    modes = tuple(dict.fromkeys(m.strip() for m in text.split(",")))  # duplicates dropped, order kept
    if not modes or any(m not in golden.MODES for m in modes):
        raise argparse.ArgumentTypeError(f"modes are a comma-separated subset of {','.join(golden.MODES)}")
    return modes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Ops agent harness")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="start a run and follow it")
    run.add_argument("objective")
    run.add_argument("--llm", choices=["fake", "openai"])
    run.add_argument("--faults", type=_json_object, help="fault injection as a JSON object")
    run.add_argument("--max-steps", type=int)
    run.add_argument("--no-eval", action="store_true", help="no online evaluation")
    listing = commands.add_parser("list", help="recent runs, newest first")
    listing.add_argument("--limit", type=_limit, default=20)
    commands.add_parser("show", help="one run as JSON").add_argument("run_id")
    commands.add_parser("resume", help="continue an interrupted run").add_argument("run_id")
    commands.add_parser("ingest", help="index the knowledge base").add_argument(
        "--force", action="store_true", help="rebuild even when unchanged"
    )
    commands.add_parser("eval", help="golden-set evaluation").add_argument("--modes", type=_modes, default=golden.MODES)
    return parser


async def amain(argv: list[str] | None = None) -> int:
    """0 when the command did its job, 1 when it did not, 2 for bad arguments or input."""
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:  # argparse printed the message (or the help)
        return exc.code if isinstance(exc.code, int) else 2
    runner = await Runner.open(settings.db_path)
    try:
        return await COMMANDS[args.command](runner, args)
    finally:
        await runner.close()


async def cmd_run(runner: Runner, args) -> int:
    options: dict = {}
    if args.faults is not None:
        options["faults"] = args.faults
    if args.max_steps is not None:
        options["limits"] = {"max_steps": args.max_steps}
    if args.no_eval:
        options["evaluate"] = False
    try:
        run = await runner.create_run(args.objective, llm=args.llm, options=options or None)
    except ValueError as exc:
        message = describe(exc) if isinstance(exc, ValidationError) else str(exc)
        print(f"error: {runner.tracer.mask(message)}", file=sys.stderr)
        return 2
    await runner.audit(run["id"], current_user(), "create_run", run["id"])
    print(f"run {run['id']}")
    return await follow(runner, run["id"], partial(runner.run_segment, run["id"]))


async def cmd_list(runner: Runner, args) -> int:
    for run in await runner.store.list_runs(args.limit):
        objective = runner.tracer.mask(run["objective"])[:60]
        print(f"{run['id']}  {run['status']:<17} {run['created_at']}  {objective}")
    return 0


async def cmd_show(runner: Runner, args) -> int:
    detail = await runner.run_detail(args.run_id)
    if detail is None:
        print(f"run {args.run_id} not found", file=sys.stderr)
        return 1
    print(json.dumps(runner.tracer.mask(detail), indent=2, ensure_ascii=False))
    return 0


async def cmd_resume(runner: Runner, args) -> int:
    try:
        await runner.request_resume(args.run_id, actor=current_user())
    except (NotFound, Conflict) as exc:
        print(exc, file=sys.stderr)
        return 1
    return await follow(runner, args.run_id, partial(runner.continue_run, args.run_id))


async def cmd_ingest(runner: Runner, args) -> int:
    try:
        result = await ingest(qdrant.get_kb(), settings.data_dir / "kb", force=args.force)
    except Exception as exc:
        print(runner.tracer.mask(f"ingest failed: {type(exc).__name__}: {exc}"), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


async def cmd_eval(runner: Runner, args) -> int:
    kb = await qdrant.available_kb()
    if kb is None:
        print("knowledge base unavailable", file=sys.stderr)
        return 1
    await runner.audit(None, current_user(), "start_eval", cfg.eval.golden_set)

    async def progress(done: int, total: int) -> None:
        print(f"{done}/{total}", flush=True)

    try:
        report = await golden.run_golden(kb, metrics.get_judge(), runner.store, modes=args.modes, progress=progress)
    except Exception as exc:
        print(runner.tracer.mask(f"evaluation failed: {type(exc).__name__}: {exc}"), file=sys.stderr)
        return 1
    print(json.dumps(report["summary"], indent=2))
    return 0


COMMANDS = {
    "run": cmd_run,
    "list": cmd_list,
    "show": cmd_show,
    "resume": cmd_resume,
    "ingest": cmd_ingest,
    "eval": cmd_eval,
}


async def follow(runner: Runner, run_id: str, segment: Callable[[], Awaitable[str]]) -> int:
    """Print the run's events while each segment runs; ask at every approval; 0 when it ends `completed`."""
    events = runner.tracer.subscribe(run_id)
    try:
        while True:
            printer = asyncio.create_task(_print_forever(events))
            try:
                status = await segment()
            finally:
                printer.cancel()
                await asyncio.gather(printer, return_exceptions=True)
            while not events.empty():  # what the printer had not reached, in order
                _print_event(events.get_nowait())
            if status != RunStatus.AWAITING_APPROVAL:
                break
            if (code := await _ask(runner, run_id)) is not None:
                return code
            segment = partial(runner.continue_run, run_id)
    finally:
        runner.tracer.unsubscribe(run_id, events)
    final = (await runner.store.get_run(run_id))["final"]
    print(f"status: {status}")
    if final:
        print(runner.tracer.mask(final))
    return 0 if status == RunStatus.COMPLETED else 1


async def _print_forever(events: asyncio.Queue) -> None:
    while True:
        _print_event(await events.get())


def _print_event(event: dict) -> None:
    where = event["tool"] or event["node"] or "-"
    note = f" [{event['attention']}]" if event["attention"] else ""
    print(f"{event['seq']:>5} {event['kind']:<8} {where:<22} {event['msg'] or ''}{note}", flush=True)


async def _ask(runner: Runner, run_id: str) -> int | None:
    """One decision at the prompt. None when decided; an exit code when the command must stop."""
    pending = await runner.store.list_approvals(run_id=run_id, status="pending")  # one at a time
    if not pending:  # another process (the API) decided, expired or cancelled it after the pause
        print(f"run {run_id} has no pending approval (decided, expired or cancelled elsewhere)")
        return 1
    approval = pending[0]
    print(f"approval {approval['id']}: {approval['tool']} {json.dumps(runner.tracer.mask(approval['args']))}")
    print(f"expires at {approval['expires_at']}")
    try:
        while True:
            answer = (await _input("[a]pprove / [r]eject / [e]dit: ")).strip().lower()
            reason = args = None
            if answer in ("a", "approve"):
                decision = "approve"
            elif answer in ("r", "reject"):
                decision, reason = "reject", ""
                while not reason.strip():
                    reason = await _input("Reason: ")
            elif answer in ("e", "edit"):
                decision = "edit"
                while args is None:
                    try:
                        args = json.loads(await _input("Arguments as JSON: "))
                    except json.JSONDecodeError as exc:
                        print(f"not valid JSON: {exc}")
            else:
                continue
            try:
                await runner.decide(
                    run_id, approval["id"], decision=decision, reason=reason, args=args, actor=current_user()
                )
                return None
            except Conflict as exc:  # decided elsewhere, cancelled, or expired by an API sweep
                print(exc)
                return 1
            except ValueError as exc:  # invalid edited args or a reason too long: the approval stays pending
                print(exc)
    except EOFError:
        print(f"\napproval {approval['id']} left pending")
        return 1


async def _input(prompt: str) -> str:
    """`input()` in a daemon thread, off the event loop. End of input raises EOFError.

    Not `asyncio.to_thread`: its worker is joined at exit, so Ctrl+C at a prompt would hang until Enter.
    """
    loop = asyncio.get_running_loop()
    answer = loop.create_future()

    def settle(result: str | None, error: Exception | None) -> None:
        if answer.done():  # the prompt was cancelled; nobody waits for it
            return
        if error is not None:
            answer.set_exception(error)
        else:
            answer.set_result(result)

    def read() -> None:
        try:
            result, error = input(prompt), None
        except Exception as exc:  # EOFError at end of input
            result, error = None, exc
        try:
            loop.call_soon_threadsafe(settle, result, error)
        except RuntimeError:  # the loop has closed: the command is over
            pass

    threading.Thread(target=read, name="cli-input", daemon=True).start()
    return await answer


def main(argv: list[str] | None = None) -> int:
    log.setup("WARNING", "text", settings.secrets())
    logging.getLogger("app.trace").disabled = True  # the CLI prints every event itself
    try:
        return asyncio.run(amain(argv))
    except KeyboardInterrupt:
        # Only the API recovers runs (ADR 0013): a run stopped mid-segment stays `running` until it starts.
        print("\ninterrupted; a run stopped mid-step stays running until the API recovers it", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
