import random

import pytest
from pydantic import ValidationError

from app.harness import tool_gateway
from app.harness.retry import backoff
from app.tools.faults import Faults, ToolFault, hits

INCIDENT = {"title": "orders-db is down", "description": "Every query fails since 09:00.", "severity": "SEV1"}


def test_backoff_full_jitter_bounds():
    random.seed(7)
    for attempt in range(1, 8):
        ceiling = min(2.0, 0.2 * 2 ** (attempt - 1))
        samples = [backoff(attempt, 0.2, 2.0) for _ in range(200)]
        assert all(0 <= s <= ceiling for s in samples)
        assert max(samples) > ceiling / 2  # full jitter spreads over the whole range
    assert backoff(3, 0.0, 0.0) == 0


def test_faults_hit_first_times_attempts():
    faults = Faults.model_validate(
        {"get_service_status": {"mode": "timeout", "times": 2}, "llm": {"mode": "malformed"}}
    )
    status = faults.for_tool("get_service_status")
    assert [hits(status, n) for n in range(4)] == [True, True, False, False]
    assert faults.llm.times == 1 and [hits(faults.llm, n) for n in range(2)] == [True, False]
    assert faults.for_tool("create_incident") is None and not hits(None, 0)
    assert faults.for_tool("llm") is None  # only tool keys


@pytest.mark.parametrize(
    "raw",
    [
        {"unknown_tool": {"mode": "timeout"}},
        {"get_service_status": {"mode": "explode"}},
        {"llm": {"mode": "bad_output"}},
        {"get_service_status": {"mode": "timeout", "times": 0}},
        {"get_service_status": {"mode": "timeout_after_commit"}},
        {"embeddings": {"mode": "timeout"}},
    ],
)
def test_unknown_fault_rejected(raw):
    with pytest.raises(ValidationError):
        Faults.model_validate(raw)


def test_timeout_after_commit_allowed_for_incidents():
    assert Faults.model_validate({"create_incident": {"mode": "timeout_after_commit"}}).create_incident.times == 1


def test_fault_defaults_are_times_1_and_ms_1000():
    faults = Faults.model_validate({"get_service_status": {"mode": "latency"}, "llm": {"mode": "timeout"}})
    assert (faults.get_service_status.times, faults.get_service_status.ms) == (1, 1000)
    assert faults.llm.times == 1


@pytest.mark.parametrize(("times", "ok", "attempts"), [(1, True, 2), (2, False, 2)])
async def test_timeout_after_commit_creates_one_incident(tracer, store, short_timeouts, times, ok, attempts):
    envelope, made = await tool_gateway.execute(
        {"id": "s2c0", "name": "create_incident", "args": INCIDENT},
        run_id="r1",
        decision={"decision": "approve"},
        attempts_before=0,
        fault=ToolFault(mode="timeout_after_commit", times=times),
        store=store,
        tracer=tracer,
    )
    assert (envelope["ok"], made) == (ok, attempts)
    [row] = await store.list_incidents()  # the retry found the committed row by its key
    assert row["idempotency_key"] == "r1:s2c0"
    if ok:
        assert envelope["data"]["incident_id"] == row["id"]
    else:
        assert envelope["error"]["type"] == "timeout"


async def test_timeout_after_commit_event_trail(tracer, store, short_timeouts):
    # times=1: attempt 1 commits then hangs into a timeout (no attention, it will be retried);
    # the retry is a warning; attempt 2 finds the committed row and succeeds (no attention).
    envelope, made = await tool_gateway.execute(
        {"id": "s2c0", "name": "create_incident", "args": INCIDENT},
        run_id="r1",
        decision={"decision": "approve"},
        attempts_before=0,
        fault=ToolFault(mode="timeout_after_commit", times=1),
        store=store,
        tracer=tracer,
    )
    assert envelope["ok"] and made == 2
    trail = [(e["kind"], e["status"], e["attention"]) for e in await store.list_events("r1")]
    assert trail == [
        ("tool", "timeout", None),
        ("retry", "retry", "warn"),
        ("tool", "ok", "success"),
    ]
