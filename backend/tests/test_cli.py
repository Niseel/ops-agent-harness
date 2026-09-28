import asyncio
import json
import threading

import pytest
from conftest import FakeJudge

from app import cli
from app.config import settings
from app.eval import metrics
from app.harness.store import Store

INCIDENT = {"title": "payments-api is degraded", "description": "p95 2400 ms, error rate 12%.", "severity": "SEV2"}
ASK_INCIDENT = "payments-api is slow. Open an incident if it is degraded."


@pytest.fixture
def answers(monkeypatch, capsys):
    """Feed `input()`: each prompt takes the next answer; no answers left means end of input."""
    queue: list[str] = []

    def fake_input(prompt: str = "") -> str:
        print(prompt, end="")
        if not queue:
            raise EOFError
        return queue.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)
    return queue


@pytest.fixture
async def db():
    """The CLI's database (settings.db_path, a tmp file), opened after the command ran."""
    stores = []

    async def open_store() -> Store:
        stores.append(await Store.open(settings.db_path))
        return stores[-1]

    yield open_store
    for store in stores:
        await store.close()


def run_id_from(out: str) -> str:
    return next(line.split()[1] for line in out.splitlines() if line.startswith("run "))


async def test_cli_run_visible_in_api(api, capsys):
    assert await cli.amain(["run", "Check payments-api", "--llm", "fake", "--no-eval"]) == 0
    out = capsys.readouterr().out
    run_id = run_id_from(out)
    assert "status: completed" in out and " done " in out
    [listed] = (await api.get("/api/runs")).json()
    assert (listed["id"], listed["status"]) == (run_id, "completed")
    trace = (await api.get(f"/api/runs/{run_id}/trace")).json()["events"]
    assert trace[0]["data"] == {"actor": "anonymous", "action": "create_run", "entity_id": run_id}


async def test_cli_faults_refused_when_disabled(monkeypatch, capsys, db):
    monkeypatch.setattr(settings, "allow_fault_injection", False)
    faults = json.dumps({"get_service_status": {"mode": "timeout"}})
    assert await cli.amain(["run", "Check payments-api", "--llm", "fake", "--faults", faults]) == 2
    assert "error:" in capsys.readouterr().err
    assert await (await db()).list_runs() == []


async def test_cli_interactive_approve(answers, capsys, db):
    answers.append("a")
    assert await cli.amain(["run", ASK_INCIDENT, "--llm", "fake", "--no-eval"]) == 0
    out = capsys.readouterr().out
    assert "[a]pprove / [r]eject / [e]dit: " in out and "create_incident" in out and "expires at" in out
    store = await db()
    [approval] = await store.list_approvals()
    assert approval["status"] == "approved" and approval["decided_by"] == "anonymous"
    assert len(await store.list_incidents()) == 1


async def test_cli_reject_and_edit_validate_input(answers, capsys, db):
    answers.extend(["x", "r", "", "  ", "not needed"])  # unknown answer, then blank reasons are asked again
    assert await cli.amain(["run", ASK_INCIDENT, "--llm", "fake", "--no-eval"]) == 0
    out = capsys.readouterr().out
    assert out.count("Reason: ") == 3
    edited = {**INCIDENT, "title": "payments-api p95 is high"}
    answers.extend(["e", "{bad", '{"title": ""}', "e", json.dumps(edited)])
    assert await cli.amain(["run", ASK_INCIDENT, "--llm", "fake", "--no-eval"]) == 0
    out = capsys.readouterr().out
    assert "not valid JSON" in out and out.count("Arguments as JSON: ") == 3
    store = await db()
    rejected, done = await store.list_approvals()
    assert (rejected["status"], rejected["reason"]) == ("rejected", "not needed")
    assert (done["status"], done["decision"]) == ("edited", edited)
    [incident] = await store.list_incidents()
    assert incident["title"] == "payments-api p95 is high"


async def test_cli_eof_leaves_approval_pending(answers, capsys, db):
    assert await cli.amain(["run", ASK_INCIDENT, "--llm", "fake", "--no-eval"]) == 1
    [approval] = await (await db()).list_approvals()
    assert approval["status"] == "pending"
    assert f"approval {approval['id']} left pending" in capsys.readouterr().out


async def test_cli_list_show_resume(capsys, db):
    assert await cli.amain(["run", "Check payments-api", "--llm", "fake", "--no-eval"]) == 0
    run_id = run_id_from(capsys.readouterr().out)
    assert await cli.amain(["list", "--limit", "5"]) == 0
    [line] = capsys.readouterr().out.splitlines()
    assert line.startswith(run_id) and "completed" in line
    assert await cli.amain(["show", run_id]) == 0
    detail = json.loads(capsys.readouterr().out)
    assert detail["id"] == run_id and detail["usage"].keys() == {"prompt_tokens", "completion_tokens"}
    assert await cli.amain(["show", "nope"]) == 1
    assert await cli.amain(["resume", run_id]) == 1  # completed, not interrupted
    assert "not interrupted" in capsys.readouterr().err
    store = await db()
    await store.update_run(run_id, status="interrupted")  # as after a crash
    assert await cli.amain(["resume", run_id]) == 0
    assert "status: completed" in capsys.readouterr().out


async def test_cli_exit_codes(capsys):
    assert await cli.amain([]) == 2
    assert await cli.amain(["run"]) == 2  # no objective
    assert await cli.amain(["nope"]) == 2
    assert await cli.amain(["run", "x", "--faults", "{bad"]) == 2
    assert await cli.amain(["list", "--limit", "0"]) == 2
    assert await cli.amain(["eval", "--modes", "hybrid,fuzzy"]) == 2
    assert await cli.amain(["run", "   ", "--llm", "fake"]) == 2  # create_run refuses it
    # A run that ends in another status than completed.
    assert await cli.amain(["run", "Why is payments-api slow?", "--llm", "fake", "--max-steps", "1"]) == 1
    assert "status: limit_exceeded" in capsys.readouterr().out


async def test_cli_does_not_recover(db):
    store = await db()
    run = await store.create_run(id="r1", objective="left running", llm_mode="fake", model="fake", options={})
    assert await cli.amain(["list"]) == 0
    assert (await store.get_run(run["id"]))["status"] == "running"


async def test_cli_run_waits_for_online_evaluation(kb, monkeypatch, capsys, db):
    monkeypatch.setattr(metrics, "get_judge", lambda: FakeJudge())
    assert await cli.amain(["run", "Why is payments-api slow?", "--llm", "fake"]) == 0
    run_id = run_id_from(capsys.readouterr().out)
    assert await (await db()).list_evals(run_id)  # stored before the command returned


async def test_cli_ingest_and_eval(kb, monkeypatch, capsys, db):
    assert await cli.amain(["ingest"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "skipped"  # the kb fixture indexed it
    assert await cli.amain(["ingest", "--force"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "rebuilt"
    monkeypatch.setattr(metrics, "get_judge", lambda: FakeJudge(reachable=False))
    assert await cli.amain(["eval", "--modes", "sparse,sparse"]) == 0
    out = capsys.readouterr().out
    assert "1/16" in out and "16/16" in out and "17/" not in out
    summary = json.loads(out[out.index("{") :])
    assert list(summary) == ["sparse"]
    assert (await (await db()).latest_eval_report())["summary"] == summary


async def test_cli_ingest_and_eval_without_kb(capsys):
    assert await cli.amain(["ingest"]) == 1
    assert await cli.amain(["eval"]) == 1
    assert "knowledge base unavailable" in capsys.readouterr().err


async def test_cli_resume_unknown_run(capsys):
    assert await cli.amain(["resume", "nope"]) == 1
    assert "not found" in capsys.readouterr().err


async def test_cli_decide_conflict_when_decided_elsewhere(monkeypatch, capsys, db):
    """A second decision (e.g. from the API) between the prompt and the answer is a 409-like Conflict."""

    def fake_input(prompt: str = "") -> str:
        print(prompt, end="")
        if not prompt.startswith("[a]"):
            raise EOFError

        async def decide_elsewhere() -> None:
            store = await Store.open(settings.db_path)
            try:
                [approval] = await store.list_approvals(status="pending")
                await store.decide_approval(
                    approval["id"], status="approved", decision=None, reason=None, decided_by="someone-else"
                )
            finally:
                await store.close()

        asyncio.run(decide_elsewhere())  # a fresh loop in this thread, off the CLI's own event loop
        return "a"

    monkeypatch.setattr("builtins.input", fake_input)
    assert await cli.amain(["run", ASK_INCIDENT, "--llm", "fake", "--no-eval"]) == 1
    out = capsys.readouterr().out
    assert "is approved" in out
    store = await db()
    [approval] = await store.list_approvals()
    assert (approval["status"], approval["decided_by"]) == ("approved", "someone-else")


async def test_cli_show_masks_secret(monkeypatch, capsys):
    secret = 'sk-live-"quoted"-secret'
    monkeypatch.setattr(settings, "llm_api_key", secret)
    assert await cli.amain(["run", f"Check payments-api, key {secret}", "--llm", "fake", "--no-eval"]) == 0
    run_id = run_id_from(capsys.readouterr().out)
    assert await cli.amain(["show", run_id]) == 0
    out = capsys.readouterr().out
    assert secret not in out
    assert json.dumps(secret)[1:-1] not in out  # the quote inside the secret, JSON-escaped


async def test_cli_prompt_does_not_block_exit(monkeypatch):
    # Ctrl+C cancels the command; the thread stuck in input() must not keep the process alive (reviewer T6).
    release = threading.Event()
    monkeypatch.setattr("builtins.input", lambda prompt="": release.wait(5) and "")
    prompt = asyncio.create_task(cli._input("? "))
    await asyncio.sleep(0.05)
    prompt.cancel()
    async with asyncio.timeout(1):
        with pytest.raises(asyncio.CancelledError):
            await prompt
    readers = [t for t in threading.enumerate() if t.name == "cli-input"]
    assert readers and all(t.daemon for t in readers)
    release.set()


async def test_cli_output_masks_secrets(answers, monkeypatch, capsys):
    secret = "sk-cli-secret-9"
    monkeypatch.setattr(settings, "llm_api_key", secret)
    answers.append("a")
    assert await cli.amain(["run", f"{ASK_INCIDENT} Key {secret}.", "--llm", "fake", "--no-eval"]) == 0
    assert await cli.amain(["list"]) == 0
    out = capsys.readouterr().out
    assert "create_incident {" in out and secret not in out and "***" in out


async def test_cli_prompt_when_approval_gone(runner, monkeypatch, capsys):
    # Another process cancelled the run between the pause and the prompt: a message and exit 1, not a traceback.
    real = cli.Runner.run_segment

    async def paused_then_cancelled(self, run_id, **kwargs):
        status = await real(self, run_id, **kwargs)
        await runner.cancel(run_id, actor="api")  # the runner fixture stands in for the API process
        return status

    monkeypatch.setattr(cli.Runner, "run_segment", paused_then_cancelled)
    assert await cli.amain(["run", ASK_INCIDENT, "--llm", "fake", "--no-eval"]) == 1
    assert "has no pending approval" in capsys.readouterr().out
