import asyncio
import json
from dataclasses import replace

import pytest
from conftest import SECRET

from app.config import cfg, settings
from app.harness import tool_gateway
from app.tools import registry
from app.tools.faults import ToolFault

APPROVE = {"decision": "approve"}
INCIDENT = {"title": "payments-api is degraded", "description": "p95 2400 ms, error rate 12%.", "severity": "SEV2"}


async def call(tracer, store, name, args, *, id="c1", decision=None, before=0, fault=None):
    return await tool_gateway.execute(
        {"id": id, "name": name, "args": args},
        run_id="r1",
        decision=decision,
        attempts_before=before,
        fault=ToolFault.model_validate(fault) if fault else None,
        store=store,
        tracer=tracer,
    )


async def events(store, kind=None):
    return [e for e in await store.list_events("r1") if kind is None or e["kind"] == kind]


def test_tool_schemas_exposed():
    defs = {d["function"]["name"]: d for d in registry.openai_tools()}
    assert set(defs) == {"get_service_status", "create_incident"}
    for d in defs.values():
        assert d["type"] == "function" and d["function"]["description"]
        schema = d["function"]["parameters"]
        assert schema["additionalProperties"] is False
        assert "idempotency_key" not in json.dumps(schema)
    assert defs["create_incident"]["function"]["parameters"]["required"] == ["title", "description", "severity"]
    assert registry.TOOLS["create_incident"].requires_approval
    assert not registry.TOOLS["get_service_status"].requires_approval


async def test_status_returns_fixture_record(tracer, store):
    envelope, attempts = await call(tracer, store, "get_service_status", {"service_name": "payments-api"})
    assert attempts == 1
    assert envelope == {
        "ok": True,
        "data": {
            "service": "payments-api",
            "status": "degraded",
            "latency_p95_ms": 2400,
            "error_rate": 0.12,
            "updated_at": "2026-09-27T09:00:00.000Z",
        },
    }
    [event] = await events(store, "tool")
    assert (event["node"], event["tool"], event["status"], event["attention"]) == (
        "tools",
        "get_service_status",
        "ok",
        None,
    )
    assert event["data"]["tool_call_id"] == "c1" and event["data"]["attempt"] == 1
    assert event["data"]["args"] == {"service_name": "payments-api"} and event["data"]["result"] == envelope


async def test_every_fixture_record_passes_the_output_model(tracer, store):
    records = json.loads((settings.data_dir / "services.json").read_text())
    assert {"payments-api", "auth-service", "orders-db"} <= {r["service"] for r in records}
    for record in records:
        envelope, _ = await call(tracer, store, "get_service_status", {"service_name": record["service"]})
        assert envelope == {"ok": True, "data": record}


async def test_unknown_service_not_found(tracer, store):
    envelope, attempts = await call(tracer, store, "get_service_status", {"service_name": "billing-api"})
    assert attempts == 1  # not retried
    assert envelope["error"] == {"type": "not_found", "message": "unknown service 'billing-api'", "retryable": False}
    assert [e["attention"] for e in await events(store)] == ["error"]


@pytest.mark.parametrize(
    ("name", "args", "decision", "field"),
    [
        ("get_service_status", {"service_name": "payments-api", "region": "eu"}, None, "region"),
        ("get_service_status", {"service_name": 42}, None, "service_name"),
        ("get_service_status", {"service_name": "Payments API"}, None, "service_name"),
        ("get_service_status", [1, 2], None, "input"),
        ("create_incident", {**INCIDENT, "severity": "SEV9"}, APPROVE, "severity"),
        ("create_incident", {**INCIDENT, "title": "x"}, APPROVE, "title"),
    ],
)
async def test_invalid_args_not_executed(tracer, store, monkeypatch, name, args, decision, field):
    ran = []
    tool = registry.TOOLS[name]

    async def spy(*a):
        ran.append(a)

    monkeypatch.setitem(registry.TOOLS, name, replace(tool, run=spy))
    envelope, attempts = await call(tracer, store, name, args, decision=decision)
    assert (envelope["ok"], envelope["error"]["type"], attempts, ran) == (False, "validation", 0, [])
    assert field in envelope["error"]["message"]
    [event] = await events(store)
    assert (event["kind"], event["status"], event["attention"], event["data"]["attempt"]) == (
        "tool",
        "validation",
        "warn",
        0,
    )
    assert await store.list_incidents() == []


async def test_unknown_tool_refused(tracer, store):
    envelope, attempts = await call(tracer, store, "delete_database", {})
    assert (envelope["error"]["type"], attempts) == ("validation", 0)
    assert "unknown tool 'delete_database'" in envelope["error"]["message"]


@pytest.mark.parametrize(
    "decision", [None, {}, {"decision": "reject", "reason": "no"}, {"decision": "later"}, "approve", ["approve"]]
)
async def test_incident_needs_decision(tracer, store, decision):
    envelope, attempts = await call(tracer, store, "create_incident", INCIDENT, decision=decision)
    assert (envelope["ok"], envelope["error"]["type"], attempts) == (False, "blocked", 0)
    assert envelope["error"]["retryable"] is False
    assert await store.list_incidents() == []
    [event] = await events(store)
    assert (event["status"], event["attention"]) == ("blocked", "error")


@pytest.mark.parametrize("decision", [APPROVE, {"decision": "edit", "args": INCIDENT}])
async def test_approved_incident_created_with_hidden_key(tracer, store, decision):
    envelope, attempts = await call(tracer, store, "create_incident", INCIDENT, id="s3c0", decision=decision)
    assert envelope["ok"] and attempts == 1
    data = envelope["data"]
    assert data["status"] == "open" and data["incident_id"].startswith("INC-")
    [row] = await store.list_incidents()
    assert row["id"] == data["incident_id"] and row["idempotency_key"] == "r1:s3c0"
    assert {k: row[k] for k in INCIDENT} == INCIDENT
    [event] = await events(store)
    assert event["attention"] == "success"


async def test_edit_decision_runs_the_edited_args(tracer, store):
    edited = {**INCIDENT, "title": "payments-api errors", "severity": "SEV1"}
    envelope, _ = await call(tracer, store, "create_incident", INCIDENT, decision={"decision": "edit", "args": edited})
    assert envelope["ok"]
    [row] = await store.list_incidents()
    assert {k: row[k] for k in edited} == edited
    [event] = await events(store)
    assert event["data"]["args"] == edited


@pytest.mark.parametrize("edit", [{"decision": "edit"}, {"decision": "edit", "args": {**INCIDENT, "severity": "SEV9"}}])
async def test_edit_without_valid_args_never_runs_the_original(tracer, store, edit):
    envelope, attempts = await call(tracer, store, "create_incident", INCIDENT, decision=edit)
    assert (envelope["error"]["type"], attempts) == ("validation", 0)
    assert await store.list_incidents() == []


async def test_long_unknown_field_name_is_cut_in_the_message(tracer, store):
    envelope, _ = await call(tracer, store, "get_service_status", {"service_name": "payments-api", "x" * 300: 1})
    assert "x" * 50 in envelope["error"]["message"] and "x" * 51 not in envelope["error"]["message"]


async def test_transient_errors_retried_with_events(tracer, store, short_timeouts):
    envelope, attempts = await call(
        tracer, store, "get_service_status", {"service_name": "payments-api"}, fault={"mode": "timeout", "times": 2}
    )
    assert envelope["ok"] and attempts == 3
    trail = [(e["kind"], e["status"], e["attention"]) for e in await events(store)]
    assert trail == [
        ("tool", "timeout", None),
        ("retry", "retry", "warn"),
        ("tool", "timeout", None),
        ("retry", "retry", "warn"),
        ("tool", "ok", None),
    ]
    assert [e["data"]["attempt"] for e in await events(store, "tool")] == [1, 2, 3]
    retry = (await events(store, "retry"))[0]
    assert retry["data"]["reason"] == "timeout" and retry["data"]["tool_call_id"] == "c1"


@pytest.mark.parametrize(("mode", "error_type"), [("timeout", "timeout"), ("error", "unavailable")])
async def test_all_attempts_fail_returns_error_envelope(tracer, store, short_timeouts, mode, error_type):
    envelope, attempts = await call(
        tracer, store, "get_service_status", {"service_name": "payments-api"}, fault={"mode": mode, "times": 5}
    )
    assert attempts == cfg.tool("get_service_status").max_attempts == 3
    assert envelope["ok"] is False and envelope["error"]["type"] == error_type and envelope["error"]["retryable"]
    tools = await events(store, "tool")
    assert [e["attention"] for e in tools] == [None, None, "error"]
    assert len(await events(store, "retry")) == 2


async def test_fault_counts_from_run_attempts(tracer, store):
    # Two attempts already used in the run: a times-2 fault has nothing left to hit.
    envelope, attempts = await call(
        tracer,
        store,
        "get_service_status",
        {"service_name": "orders-db"},
        before=2,
        fault={"mode": "error", "times": 2},
    )
    assert envelope["ok"] and attempts == 1


async def test_bad_output_not_retried(tracer, store):
    envelope, attempts = await call(
        tracer, store, "get_service_status", {"service_name": "payments-api"}, fault={"mode": "bad_output", "times": 3}
    )
    assert attempts == 1
    assert envelope["error"]["type"] == "bad_output" and not envelope["error"]["retryable"]
    [event] = await events(store)
    assert (event["status"], event["attention"]) == ("bad_output", "warn")


async def test_bad_output_fault_does_not_create_an_incident(tracer, store):
    envelope, _ = await call(tracer, store, "create_incident", INCIDENT, decision=APPROVE, fault={"mode": "bad_output"})
    assert envelope["error"]["type"] == "bad_output"
    assert await store.list_incidents() == []


async def test_long_output_truncated(tracer, store, monkeypatch):
    monkeypatch.setattr(cfg.output, "max_tool_result_chars", 40)
    envelope, _ = await call(tracer, store, "get_service_status", {"service_name": "payments-api"})
    assert envelope["ok"] and envelope["truncated"] is True
    assert isinstance(envelope["data"], str) and len(envelope["data"]) == 40
    assert (
        envelope["data"]
        == json.dumps(
            {
                "service": "payments-api",
                "status": "degraded",
                "latency_p95_ms": 2400,
                "error_rate": 0.12,
                "updated_at": "2026-09-27T09:00:00.000Z",
            }
        )[:40]
    )


async def test_short_output_not_marked_truncated(tracer, store):
    envelope, _ = await call(tracer, store, "get_service_status", {"service_name": "payments-api"})
    assert "truncated" not in envelope


async def test_latency_fault_below_timeout_succeeds(tracer, store):
    envelope, attempts = await call(
        tracer, store, "get_service_status", {"service_name": "payments-api"}, fault={"mode": "latency", "ms": 20}
    )
    assert envelope["ok"] and attempts == 1
    [event] = await events(store)
    assert event["data"]["duration_ms"] >= 20


async def test_latency_fault_above_timeout_times_out(tracer, store, short_timeouts):
    envelope, attempts = await call(
        tracer, store, "get_service_status", {"service_name": "payments-api"}, fault={"mode": "latency", "ms": 200}
    )
    assert envelope["ok"] and attempts == 2  # attempt 1 hit the 0.05 s timeout; attempt 2 has no fault
    assert [e["status"] for e in await events(store, "tool")] == ["timeout", "ok"]


async def test_fault_counts_from_run_attempts_across_calls(tracer, store):
    # 1 attempt already used earlier in the run: a times-2 fault has exactly one hit left,
    # so this call's first attempt fails and its second (retried) attempt succeeds.
    envelope, attempts = await call(
        tracer,
        store,
        "get_service_status",
        {"service_name": "orders-db"},
        before=1,
        fault={"mode": "error", "times": 2},
    )
    assert envelope["ok"] and attempts == 2
    assert [e["status"] for e in await events(store, "tool")] == ["unavailable", "ok"]


async def test_validation_message_does_not_echo_rejected_value(tracer, store):
    envelope, _ = await call(tracer, store, "get_service_status", {"service_name": "SECRET-VALUE-XYZ"})
    assert "SECRET-VALUE-XYZ" not in envelope["error"]["message"]


async def test_incident_output_model_rejects_bad_incident_id(tracer, store, monkeypatch):
    tool = registry.TOOLS["create_incident"]

    async def bad_run(args, ctx):
        return {"incident_id": "not-a-valid-id", "status": "open", "created_at": "2026-09-27T09:00:00.000Z"}

    monkeypatch.setitem(registry.TOOLS, tool.name, replace(tool, run=bad_run))
    envelope, attempts = await call(tracer, store, "create_incident", INCIDENT, decision=APPROVE)
    assert attempts == 1  # bad_output is not retried
    assert envelope["error"]["type"] == "bad_output"
    assert await store.list_incidents() == []


async def test_truncation_boundary_exact_length_not_truncated(tracer, store, monkeypatch):
    text = json.dumps(
        {
            "service": "payments-api",
            "status": "degraded",
            "latency_p95_ms": 2400,
            "error_rate": 0.12,
            "updated_at": "2026-09-27T09:00:00.000Z",
        }
    )
    monkeypatch.setattr(cfg.output, "max_tool_result_chars", len(text))
    envelope, _ = await call(tracer, store, "get_service_status", {"service_name": "payments-api"})
    assert "truncated" not in envelope
    assert envelope["data"] == json.loads(text)


async def test_outer_cancellation_during_attempt_is_not_swallowed(tracer, store):
    task = asyncio.create_task(
        call(tracer, store, "get_service_status", {"service_name": "payments-api"}, fault={"mode": "latency"})
    )
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_secrets_in_tool_args_masked_in_tool_events(tracer, store):
    incident = {**INCIDENT, "description": f"Leaked key {SECRET} in the logs, needs rotation now."}
    await call(tracer, store, "create_incident", incident, id="s3c1", decision=APPROVE)
    [event] = await events(store)
    assert SECRET not in json.dumps(event["data"])
    assert "***" in event["data"]["args"]["description"]


async def test_create_incident_max_attempts_is_two(tracer, store, short_timeouts):
    assert cfg.tool("create_incident").max_attempts == 2
    envelope, attempts = await call(
        tracer, store, "create_incident", INCIDENT, decision=APPROVE, fault={"mode": "timeout", "times": 5}
    )
    assert attempts == 2
    assert envelope["error"]["type"] == "timeout"
    assert await store.list_incidents() == []


async def test_crashing_tool_is_unavailable_and_retried(tracer, store, monkeypatch):
    tool = registry.TOOLS["get_service_status"]

    async def boom(args, ctx):
        raise KeyError("oops")

    monkeypatch.setitem(registry.TOOLS, tool.name, replace(tool, run=boom))
    envelope, attempts = await call(tracer, store, "get_service_status", {"service_name": "payments-api"})
    assert attempts == 3
    assert envelope["error"] == {
        "type": "unavailable",
        "message": "get_service_status failed (KeyError)",
        "retryable": True,
    }
