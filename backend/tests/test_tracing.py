import json
import logging
import re
from pathlib import Path

import pytest
from conftest import SECRET

from app import log
from app.clock import ISO_PATTERN, now_iso
from app.llm.fake import ScriptedLLM, calls, final, raw


def _json_lines(out: str) -> list[dict]:
    return [json.loads(line) for line in out.splitlines() if line.startswith("{")]


async def test_events_have_increasing_seq(tracer, store):
    before = now_iso()
    for kind in ("stage", "llm", "tool"):
        await tracer.emit("r1", kind, node="agent")
    await tracer.emit("r2", "stage")
    events = await store.list_events("r1")
    assert [e["kind"] for e in events] == ["stage", "llm", "tool"]
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == 3
    assert all(e["t_ms"] > 1_700_000_000_000 for e in events)  # Unix epoch ms
    assert all(e["created_at"] >= before and e["created_at"].endswith("Z") for e in events)
    assert await store.list_events("r1", after_seq=seqs[0]) == events[1:]


async def test_log_line_is_json_with_run_id(tracer, capsys):
    log.setup("INFO", "json", [SECRET])
    event = await tracer.emit(
        "r1",
        "tool",
        node="tools",
        tool="get_service_status",
        status="error",
        attention="error",
        msg="timed out",
        data={"attempt": 2},
    )
    line = next(entry for entry in _json_lines(capsys.readouterr().out) if entry["logger"] == "app.trace")
    assert line["level"] == "ERROR"
    assert (line["run_id"], line["seq"], line["kind"], line["tool"]) == (
        "r1",
        event["seq"],
        "tool",
        "get_service_status",
    )
    assert line["data"] == {"attempt": 2}


async def test_secrets_masked(tracer, store, capsys):
    log.setup("INFO", "json", [SECRET])
    await tracer.emit("r1", "llm", msg=f"key {SECRET} used", data={"call": [{"header": f"Bearer {SECRET}"}], "n": 1})
    stored = (await store.list_events("r1"))[0]
    assert stored["msg"] == "key *** used"
    assert stored["data"] == {"call": [{"header": "Bearer ***"}], "n": 1}
    try:
        raise RuntimeError(f"upstream rejected {SECRET}")
    except RuntimeError:
        logging.getLogger("app.test").exception("call failed")
    out = capsys.readouterr().out
    assert SECRET not in out
    assert any("upstream rejected ***" in line.get("exc", "") for line in _json_lines(out))


async def test_mask_walks_tuples_and_leaves_non_strings_untouched(tracer):
    masked = tracer.mask(
        {"header": (f"Bearer {SECRET}", "kept"), "items": [f"id={SECRET}", 42], "n": 3, "ok": True, "nothing": None}
    )
    assert masked == {"header": ["Bearer ***", "kept"], "items": ["id=***", 42], "n": 3, "ok": True, "nothing": None}
    assert tracer.mask(42) == 42 and tracer.mask(None) is None and tracer.mask(True) is True


async def test_subscriber_gets_live_events(tracer):
    queue = tracer.subscribe("r1")
    other = tracer.subscribe("r2")
    event = await tracer.emit("r1", "stage", node="guard")
    assert queue.get_nowait() == event
    assert other.empty()
    tracer.unsubscribe("r1", queue)
    await tracer.emit("r1", "stage", node="agent")
    assert queue.empty()


def test_log_masks_secrets_json_would_escape(capsys):
    secret = 'tok"en\\x'
    log.setup("INFO", "json", [secret])
    logging.getLogger("app.test").info("bad %s here", secret)
    out = capsys.readouterr().out
    assert "tok" not in out and json.loads(out.splitlines()[-1])["msg"] == "bad *** here"


# --- run level (T6) ---------------------------------------------------------------------------


async def test_events_cover_every_step(runner, search):
    run = await runner.create_run("Why is payments-api slow?")
    await runner.run_segment(run["id"])
    events = await runner.store.list_events(run["id"])
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    trail = [(e["kind"], e["node"], e["tool"]) for e in events]
    assert trail == [
        ("stage", "guard", None),
        ("stage", "agent", None),
        ("llm", "agent", None),
        ("stage", "tools", None),
        ("stage", "kb.embed", "search_knowledge_base"),
        ("stage", "kb.dense", "search_knowledge_base"),
        ("stage", "kb.bm25", "search_knowledge_base"),
        ("stage", "kb.rrf", "search_knowledge_base"),
        ("tool", "tools", "search_knowledge_base"),
        ("stage", "guard", None),
        ("stage", "agent", None),
        ("llm", "agent", None),
        ("stage", "tools", None),
        ("tool", "tools", "get_service_status"),
        ("stage", "guard", None),
        ("stage", "agent", None),
        ("llm", "agent", None),
        ("stage", "finalize", None),
        ("done", None, None),
    ]
    guard = events[9]["data"]
    assert guard == {"steps": 1, "max_steps": 8, "tool_calls": 1, "max_tool_calls": 12}
    assert all(e["run_id"] == run["id"] for e in events)


@pytest.mark.parametrize(
    ("items", "limits", "status", "attention"),
    [
        ([final("done")], None, "completed", "success"),
        ([raw(), raw(), raw()], None, "failed", "error"),
        (
            [calls(("get_service_status", {"service_name": "payments-api"}))],
            {"max_steps": 1},
            "limit_exceeded",
            "error",
        ),
    ],
)
async def test_one_done_event_per_run(runner, items, limits, status, attention):
    run = await runner.create_run("Check payments-api", options={"limits": limits} if limits else None)
    assert await runner.run_segment(run["id"], llm_client=ScriptedLLM(items)) == status
    done = [e for e in await runner.store.list_events(run["id"]) if e["kind"] == "done"]
    assert [(e["status"], e["attention"]) for e in done] == [(status, attention)]
    assert done[0]["seq"] == max(e["seq"] for e in await runner.store.list_events(run["id"]))


# --- one timestamp format (app/clock.py) ------------------------------------------------------

ISO = re.compile(ISO_PATTERN)


def test_now_iso_is_the_project_format():
    assert ISO.match(now_iso())
    assert now_iso(0) == "1970-01-01T00:00:00.000Z"
    assert now_iso(1790506537.0745) == "2026-09-27T10:55:37.074Z"


def test_now_iso_truncates_sub_millisecond_precision():
    # .0995s is 99.5ms: truncated to .099, not rounded up to .100 (which could roll into the next second).
    assert now_iso(1790506537.0995) == "2026-09-27T10:55:37.099Z"
    assert now_iso(0.9999) == "1970-01-01T00:00:00.999Z"


@pytest.mark.parametrize("fmt", ["json", "text"])
def test_log_lines_use_the_project_format(capsys, fmt):
    log.setup("INFO", fmt, [])
    logging.getLogger("app.test").info("hello")
    out = capsys.readouterr().out.strip().splitlines()[-1]
    ts = json.loads(out)["ts"] if fmt == "json" else out.split(" ", 1)[0]
    assert ISO.match(ts), ts


def test_secrets_masked_in_text_formatter_too(capsys):
    log.setup("INFO", "text", [SECRET])
    logging.getLogger("app.test").info("key %s used", SECRET)
    out = capsys.readouterr().out.strip().splitlines()[-1]
    assert SECRET not in out
    assert "key *** used" in out
    assert ISO.match(out.split(" ", 1)[0])


async def test_stored_timestamps_use_the_project_format(runner, search):
    run = await runner.create_run("orders-db is down. Open an incident.")
    await runner.run_segment(run["id"])
    row = await runner.store.get_run(run["id"])
    stamps = [row["created_at"], row["updated_at"]]
    stamps += [e["created_at"] for e in await runner.store.list_events(run["id"])]
    incident = await runner.store.create_incident(
        idempotency_key="k1",
        run_id=run["id"],
        title="orders-db is down",
        description="All queries fail.",
        severity="SEV1",
    )
    stamps.append(incident["created_at"])
    assert all(ISO.match(s) for s in stamps), stamps


async def test_finished_at_of_a_completed_run_uses_the_project_format(runner, search):
    run = await runner.create_run("Why is payments-api slow?")
    status = await runner.run_segment(run["id"])
    row = await runner.store.get_run(run["id"])
    assert status == "completed"
    assert row["finished_at"] is not None and ISO.match(row["finished_at"])


def test_only_clock_formats_timestamps():
    # One place makes timestamps; anywhere else would drift from the project format.
    app = Path(__file__).resolve().parents[1] / "app"
    # fromisoformat( parses, it does not format, so it is allowed.
    pattern = re.compile(
        r"(?<!from)isoformat\(|strftime\(|datetime\.now\(|utcnow\(|fromtimestamp\(|\b(ctime|asctime|gmtime|localtime)\("
    )
    offenders = [
        str(p.relative_to(app))
        for p in app.rglob("*.py")
        if p.relative_to(app) != Path("clock.py") and pattern.search(p.read_text())
    ]
    assert offenders == []
