import asyncio
import json
from types import SimpleNamespace

import httpx2
import pytest
from conftest import wait_for_status

from app.api import get_runner
from app.config import settings
from app.llm import fake
from app.llm.openai_compat import ToolCall
from app.main import app

INCIDENT = {"title": "payments-api is degraded", "description": "p95 2400 ms, error rate 12%.", "severity": "SEV2"}
ASK_INCIDENT = "payments-api is slow. Open an incident if it is degraded."


async def create(api, objective="Check payments-api", **body) -> str:
    response = await api.post("/api/runs", json={"objective": objective, "llm": "fake", **body})
    assert response.status_code == 202, response.text
    return response.json()["run_id"]


async def paused_run(api, runner) -> tuple[str, dict]:
    run_id = await create(api, ASK_INCIDENT)
    await wait_for_status(runner, run_id, "awaiting_approval")
    [approval] = (await api.get("/api/approvals", params={"status": "pending"})).json()
    return run_id, approval


def decide_url(run_id, approval_id):
    return f"/api/runs/{run_id}/approvals/{approval_id}"


class BlockingLLM:
    """Waits on a gate before its first reply, so a test can see the run before any step commits."""

    model = "blocking"

    def __init__(self) -> None:
        self.gate = asyncio.Event()

    async def complete(self, messages, tools):
        await self.gate.wait()
        return fake.final("done")


async def test_create_run_returns_202(api, runner, search):
    response = await api.post("/api/runs", json={"objective": "Why is payments-api slow?", "llm": "fake"})
    assert response.status_code == 202
    body = response.json()
    assert body.keys() == {"run_id", "status"} and body["status"] == "running"
    await wait_for_status(runner, body["run_id"], "completed")
    [listed] = (await api.get("/api/runs")).json()
    assert listed["id"] == body["run_id"] and listed["status"] == "completed"


@pytest.mark.parametrize(
    "body",
    [
        {"objective": "Check payments-api", "unknown_field": True},
        {"objective": ""},
        {"objective": "   "},
        {"objective": "x" * 2001},
        {"objective": "Check payments-api", "llm": "gpt"},
        {"objective": "Check payments-api", "options": {"limits": {"max_steps": 0}}},
        {"objective": "Check payments-api", "options": {"limits": {"max_tokens": 5}}},
        {"objective": "Check payments-api", "options": {"faults": {"nope": {"mode": "timeout"}}}},
        {"objective": "Check payments-api", "options": {"faults": {"get_service_status": {"mode": "explode"}}}},
        {"objective": "Check payments-api", "options": "fast"},
        {"objective": "", "unknown_field": True},
        {},
    ],
)
async def test_invalid_body_returns_422(api, body):
    response = await api.post("/api/runs", json=body)
    assert response.status_code == 422, response.text
    assert "detail" in response.json()
    assert (await api.get("/api/runs")).json() == []


async def test_faults_refused_when_disabled(api, monkeypatch):
    monkeypatch.setattr(settings, "allow_fault_injection", False)
    response = await api.post(
        "/api/runs",
        json={"objective": "Check payments-api", "options": {"faults": {"llm": {"mode": "malformed"}}}},
    )
    assert response.status_code == 422 and "fault injection is off" in response.json()["detail"]
    assert (await api.get("/api/runs")).json() == []


async def test_list_runs_newest_first_and_limit(api, runner):
    ids = [await create(api, f"Check payments-api {i}") for i in range(3)]
    for run_id in ids:
        await wait_for_status(runner, run_id, "completed")
    runs = (await api.get("/api/runs")).json()
    assert [r["id"] for r in runs] == ids[::-1]
    assert runs[0].keys() == {
        "id",
        "objective",
        "status",
        "llm_mode",
        "steps",
        "tool_calls",
        "created_at",
        "updated_at",
    }
    assert len((await api.get("/api/runs", params={"limit": 2})).json()) == 2
    for limit in (0, 101, "x"):
        assert (await api.get("/api/runs", params={"limit": limit})).status_code == 422


async def test_run_detail_has_history(api, runner, search):
    run_id, approval = await paused_run(api, runner)
    assert (await api.post(decide_url(run_id, approval["id"]), json={"decision": "approve"})).status_code == 200
    await wait_for_status(runner, run_id, "completed")
    detail = (await api.get(f"/api/runs/{run_id}")).json()
    assert detail["objective"] == ASK_INCIDENT and detail["status"] == "completed"
    assert detail["options"]["limits"]["max_steps"] == 8 and detail["options"]["evaluate"] is False
    roles = [m["role"] for m in detail["messages"]]
    assert roles[0] == "user" and roles[-1] == "assistant" and roles.count("tool") == 3
    assert [c["tool"] for c in detail["calls"]] == ["search_knowledge_base", "get_service_status", "create_incident"]
    for call in detail["calls"]:
        assert call.keys() == {"tool_call_id", "tool", "args", "attempts", "duration_ms", "status", "result"}
        assert call["attempts"] == 1 and call["status"] == "ok" and call["result"]["ok"]
    assert detail["calls"][2]["args"]["severity"] == "SEV2"
    [row] = detail["approvals"]
    assert row["id"] == approval["id"] and row["status"] == "approved"
    assert detail["evals"] == []
    assert detail["usage"] == {"prompt_tokens": 0, "completion_tokens": 0}  # the fake LLM reports none
    assert (detail["steps"], detail["tool_calls"]) == (4, 3)
    missing = await api.get("/api/runs/nope")
    assert missing.status_code == 404 and missing.json() == {"detail": "run nope not found"}


async def test_run_detail_survives_restart(api, runner, search, tmp_path):
    run_id = await create(api, "Why is payments-api slow?")
    await wait_for_status(runner, run_id, "completed")
    before = (await api.get(f"/api/runs/{run_id}")).json()
    await runner.close()
    reopened = await type(runner).open(tmp_path / "harness.db")  # a new process on the same file
    app.dependency_overrides[get_runner] = lambda: reopened
    try:
        assert (await api.get(f"/api/runs/{run_id}")).json() == before
    finally:
        await reopened.close()


async def test_list_pending_approvals(api, runner, search):
    run_id, approval = await paused_run(api, runner)
    assert approval.keys() == {
        "id",
        "run_id",
        "tool_call_id",
        "tool",
        "args",
        "status",
        "decision",
        "reason",
        "decided_by",
        "created_at",
        "decided_at",
        "expires_at",
    }
    assert (approval["run_id"], approval["tool"], approval["status"]) == (run_id, "create_incident", "pending")
    assert (await api.get("/api/approvals")).json() == [approval]
    assert (await api.get("/api/approvals", params={"status": "approved"})).json() == []
    assert (await api.get("/api/approvals", params={"status": "maybe"})).status_code == 422


async def test_invalid_edit_returns_422(api, runner, search):
    run_id, approval = await paused_run(api, runner)
    bad = await api.post(
        decide_url(run_id, approval["id"]), json={"decision": "edit", "args": {**INCIDENT, "severity": "SEV9"}}
    )
    assert bad.status_code == 422 and "severity" in bad.json()["detail"]
    assert (await api.get("/api/approvals", params={"status": "pending"})).json()[0]["id"] == approval["id"]
    good = await api.post(decide_url(run_id, approval["id"]), json={"decision": "edit", "args": INCIDENT})
    assert good.status_code == 200 and good.json()["status"] == "edited"
    await wait_for_status(runner, run_id, "completed")


async def test_second_decision_returns_409(api, runner, search):
    run_id, approval = await paused_run(api, runner)
    assert (await api.post(decide_url(run_id, approval["id"]), json={"decision": "approve"})).status_code == 200
    second = await api.post(decide_url(run_id, approval["id"]), json={"decision": "reject", "reason": "late"})
    assert second.status_code == 409 and "detail" in second.json()
    await wait_for_status(runner, run_id, "completed")


@pytest.mark.parametrize(
    "body",
    [
        {"decision": "reject"},
        {"decision": "reject", "reason": "   "},
        {"decision": "reject", "reason": "x" * 501},
        {"decision": "approve", "args": INCIDENT},
        {"decision": "reject", "reason": "no", "args": INCIDENT},
        {"decision": "edit"},
        {"decision": "approve", "note": "hi"},
        {"decision": "maybe"},
        {},
    ],
)
async def test_decision_body_rules(api, runner, search, body):
    run_id, approval = await paused_run(api, runner)
    response = await api.post(decide_url(run_id, approval["id"]), json=body)
    assert response.status_code == 422, response.text
    assert (await runner.store.get_approval(approval["id"]))["status"] == "pending"


async def test_decision_on_unknown_or_mismatched_ids_returns_404(api, runner, search):
    run_id, approval = await paused_run(api, runner)
    assert (await api.post(decide_url(run_id, "nope"), json={"decision": "approve"})).status_code == 404
    assert (await api.post(decide_url("other", approval["id"]), json={"decision": "approve"})).status_code == 404


async def test_cancel_final_run_returns_409(api, runner, search):
    run_id, approval = await paused_run(api, runner)
    cancelled = await api.post(f"/api/runs/{run_id}/cancel")
    assert cancelled.status_code == 200 and cancelled.json() == {"run_id": run_id, "status": "cancelled"}
    assert (await runner.store.get_approval(approval["id"]))["status"] == "cancelled"
    assert (await api.post(decide_url(run_id, approval["id"]), json={"decision": "approve"})).status_code == 409
    assert (await api.post(f"/api/runs/{run_id}/cancel")).status_code == 409
    assert (await api.post("/api/runs/nope/cancel")).status_code == 404


async def test_approver_token_required(api, runner, search, monkeypatch):
    monkeypatch.setattr(settings, "approver_token", "approve-me-123")
    run_id, approval = await paused_run(api, runner)
    url = decide_url(run_id, approval["id"])
    missing = await api.post(url, json={"decision": "approve"})
    assert missing.status_code == 401 and missing.json() == {"detail": "missing or wrong X-Approver-Token"}
    wrong = await api.post(url, json={"decision": "approve"}, headers={"X-Approver-Token": "nope"})
    assert wrong.status_code == 401
    non_ascii = await api.post(
        url, json={"decision": "approve"}, headers={"X-Approver-Token": "é".encode()}
    )  # non-ASCII bytes
    assert non_ascii.status_code == 401
    before_body = await api.post(url, json={"decision": "maybe"})  # 401 before the body's 422
    assert before_body.status_code == 401
    assert (await api.post(decide_url(run_id, "nope"), json={"decision": "approve"})).status_code == 401  # and 404
    right = await api.post(url, json={"decision": "approve"}, headers={"X-Approver-Token": "approve-me-123"})
    assert right.status_code == 200
    await wait_for_status(runner, run_id, "completed")


async def test_no_token_needed_when_unset(api, runner, search):
    run_id, approval = await paused_run(api, runner)
    response = await api.post(
        decide_url(run_id, approval["id"]), json={"decision": "approve"}, headers={"X-Approver-Token": ""}
    )
    assert response.status_code == 200 and response.json()["status"] == "approved"
    await wait_for_status(runner, run_id, "completed")


async def test_trace_export(api, runner, search):
    run_id = await create(api, "Why is payments-api slow?")
    await wait_for_status(runner, run_id, "completed")
    trace = (await api.get(f"/api/runs/{run_id}/trace")).json()
    assert trace.keys() == {"run_id", "status", "events"} and trace["status"] == "completed"
    seqs = [e["seq"] for e in trace["events"]]
    assert seqs == sorted(seqs)
    kinds = [e["kind"] for e in trace["events"]]
    assert kinds[0] == "log" and kinds[-1] == "done" and kinds.count("done") == 1  # the create audit comes first
    assert (await api.get("/api/runs/nope/trace")).status_code == 404


async def test_usage_sums_llm_tokens(api, runner, monkeypatch):
    status_call = (ToolCall("c0", "get_service_status", json.dumps({"service_name": "payments-api"})),)
    llm = fake.ScriptedLLM(
        [
            fake.raw(tool_calls=status_call, finish_reason="tool_calls", prompt_tokens=12, completion_tokens=5),
            fake.raw(content="done", prompt_tokens=30, completion_tokens=7),
        ]
    )
    monkeypatch.setattr(runner, "_client", lambda mode: llm)
    run_id = await create(api)
    await wait_for_status(runner, run_id, "completed")
    usage = (await api.get(f"/api/runs/{run_id}")).json()["usage"]
    assert usage == {"prompt_tokens": 42, "completion_tokens": 12}


async def test_audit_event_for_state_changes(api, runner, search, monkeypatch):
    run_id, approval = await paused_run(api, runner)
    await api.post(decide_url(run_id, approval["id"]), json={"decision": "reject", "reason": "not now"})
    await wait_for_status(runner, run_id, "completed")
    other = await create(api, "Check auth-service")
    await wait_for_status(runner, other, "completed")
    interrupted = await create(api, "Check orders-db")
    await wait_for_status(runner, interrupted, "completed")
    await runner.store.update_run(interrupted, status="interrupted")  # as after a crash
    assert (await api.post(f"/api/runs/{interrupted}/resume")).status_code == 202
    await wait_for_status(runner, interrupted, "completed")
    cancel_me = await create(api, ASK_INCIDENT)
    await wait_for_status(runner, cancel_me, "awaiting_approval")
    await api.post(f"/api/runs/{cancel_me}/cancel")
    audits = []
    for rid in (run_id, interrupted, cancel_me):
        audits += [e["data"] for e in (await api.get(f"/api/runs/{rid}/trace")).json()["events"] if e["kind"] == "log"]
    assert {(a["actor"], a["action"]) for a in audits} == {
        ("anonymous", "create_run"),
        ("anonymous", "decide_approval"),
        ("anonymous", "resume_run"),
        ("anonymous", "cancel_run"),
    }
    assert {"actor": "anonymous", "action": "decide_approval", "entity_id": approval["id"]} in audits


async def test_secrets_never_in_responses(api, runner, search, monkeypatch):
    secret = "sk-live-leak-0102030405"
    monkeypatch.setattr(settings, "llm_api_key", secret)
    run_id = await create(api, f"payments-api is slow, key {secret}. Open an incident if it is degraded.")
    await wait_for_status(runner, run_id, "awaiting_approval")
    for path in ("/api/runs", f"/api/runs/{run_id}", f"/api/runs/{run_id}/trace", "/api/approvals"):
        body = (await api.get(path)).text
        assert secret not in body, path
        assert json.dumps(secret)[1:-1] not in body
    assert "***" in (await api.get(f"/api/runs/{run_id}")).text


async def test_tools_listed(api):
    tools = (await api.get("/api/tools")).json()
    assert [t["name"] for t in tools] == ["search_knowledge_base", "get_service_status", "create_incident"]
    for tool in tools:
        assert tool.keys() == {"name", "description", "input_schema", "requires_approval"}
        assert tool["input_schema"]["additionalProperties"] is False
    assert [t["requires_approval"] for t in tools] == [False, False, True]


async def test_incidents_listed(api, runner, search):
    run_id, approval = await paused_run(api, runner)
    await api.post(decide_url(run_id, approval["id"]), json={"decision": "approve"})
    await wait_for_status(runner, run_id, "completed")
    [incident] = (await api.get("/api/incidents")).json()
    assert incident.keys() == {"id", "run_id", "title", "description", "severity", "status", "created_at"}
    assert incident["run_id"] == run_id and incident["severity"] == "SEV2"


async def test_resume_non_interrupted_returns_409(api, runner):
    run_id = await create(api)
    await wait_for_status(runner, run_id, "completed")
    response = await api.post(f"/api/runs/{run_id}/resume")
    assert response.status_code == 409 and "not interrupted" in response.json()["detail"]
    assert (await api.post("/api/runs/nope/resume")).status_code == 404


async def test_unexpected_error_gives_json_500():
    async def list_runs(limit):
        raise RuntimeError("database exploded")

    broken = SimpleNamespace(store=SimpleNamespace(list_runs=list_runs))
    app.dependency_overrides[get_runner] = lambda: broken
    transport = httpx2.ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/runs")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 500 and response.json() == {"detail": "internal error"}
    assert "exploded" not in response.text


async def test_cors_allows_configured_origin_only(api):
    origin = settings.cors_origins.split(",")[0].strip()  # what the app was built with
    allowed = await api.options("/api/runs", headers={"Origin": origin, "Access-Control-Request-Method": "POST"})
    assert allowed.headers.get("access-control-allow-origin") == origin
    other = await api.get("/api/runs", headers={"Origin": "http://evil.test"})
    assert "access-control-allow-origin" not in other.headers


async def test_run_detail_before_first_step(api, runner, monkeypatch):
    llm = BlockingLLM()
    monkeypatch.setattr(runner, "_client", lambda mode: llm)
    run_id = await create(api)
    detail = (await api.get(f"/api/runs/{run_id}")).json()
    assert detail["status"] == "running" and detail["messages"] == [] and detail["calls"] == []
    llm.gate.set()
    await wait_for_status(runner, run_id, "completed")


async def test_call_shows_zero_attempts_for_a_refused_call(api, runner, search):
    run_id, approval = await paused_run(api, runner)
    reject = await api.post(decide_url(run_id, approval["id"]), json={"decision": "reject", "reason": "not now"})
    assert reject.status_code == 200
    await wait_for_status(runner, run_id, "completed")
    detail = (await api.get(f"/api/runs/{run_id}")).json()
    [call] = [c for c in detail["calls"] if c["tool"] == "create_incident"]
    assert call["attempts"] == 0 and call["status"] == "rejected" and call["duration_ms"] == 0


async def test_call_shows_edited_args(api, runner, search):
    run_id, approval = await paused_run(api, runner)
    edited = {**INCIDENT, "severity": "SEV3"}
    good = await api.post(decide_url(run_id, approval["id"]), json={"decision": "edit", "args": edited})
    assert good.status_code == 200
    await wait_for_status(runner, run_id, "completed")
    detail = (await api.get(f"/api/runs/{run_id}")).json()
    [call] = [c for c in detail["calls"] if c["tool"] == "create_incident"]
    assert call["args"]["severity"] == "SEV3" and call["attempts"] == 1 and call["status"] == "ok"


async def test_list_runs_limit_boundaries(api, runner):
    run_id = await create(api)
    await wait_for_status(runner, run_id, "completed")
    for limit in (1, 100):
        assert (await api.get("/api/runs", params={"limit": limit})).status_code == 200


@pytest.mark.parametrize("status", ["pending", "approved", "rejected", "edited", "cancelled"])
async def test_approvals_filtered_by_each_status(api, runner, search, status):
    run_id, approval = await paused_run(api, runner)
    decision = {"pending": None, "approved": "approve", "rejected": "reject", "edited": "edit", "cancelled": None}[
        status
    ]
    if decision == "approve":
        await api.post(decide_url(run_id, approval["id"]), json={"decision": "approve"})
    elif decision == "reject":
        await api.post(decide_url(run_id, approval["id"]), json={"decision": "reject", "reason": "no"})
    elif decision == "edit":
        await api.post(decide_url(run_id, approval["id"]), json={"decision": "edit", "args": INCIDENT})
    elif status == "cancelled":
        await api.post(f"/api/runs/{run_id}/cancel")
    listed = (await api.get("/api/approvals", params={"status": status})).json()
    assert [a["id"] for a in listed] == [approval["id"]]


async def test_decide_with_reason_on_approve_kept_as_note(api, runner, search):
    run_id, approval = await paused_run(api, runner)
    response = await api.post(decide_url(run_id, approval["id"]), json={"decision": "approve", "reason": "looks fine"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved" and body["reason"] == "looks fine"
    await wait_for_status(runner, run_id, "completed")


async def test_decision_on_run_in_another_state_returns_409(api, runner, search):
    run_id, approval = await paused_run(api, runner)
    await api.post(f"/api/runs/{run_id}/cancel")
    response = await api.post(decide_url(run_id, approval["id"]), json={"decision": "approve"})
    assert response.status_code == 409 and response.json() == {"detail": f"approval {approval['id']} is cancelled"}


async def test_malformed_json_body_returns_422(api):
    headers = {"content-type": "application/json"}
    create_response = await api.post("/api/runs", content=b"{not json", headers=headers)
    assert create_response.status_code == 422
    assert isinstance(create_response.json()["detail"], list)


async def test_error_bodies_are_detail_only(api, runner, search):
    run_id, approval = await paused_run(api, runner)
    not_found = await api.get("/api/runs/nope")
    assert not_found.status_code == 404 and not_found.json() == {"detail": "run nope not found"}
    fastapi_validation = await api.get("/api/runs", params={"limit": "x"})
    assert fastapi_validation.status_code == 422 and isinstance(fastapi_validation.json()["detail"], list)
    domain_validation = await api.post(decide_url(run_id, approval["id"]), json={"decision": "maybe"})
    assert domain_validation.status_code == 422 and isinstance(domain_validation.json()["detail"], list)


async def test_secret_with_a_quote_is_masked_json_escaped(api, runner, monkeypatch):
    secret = 'sk-live-"quoted"-secret'
    monkeypatch.setattr(settings, "llm_api_key", secret)
    run_id = await create(api, f"payments-api is slow, key {secret}. Open an incident if it is degraded.")
    await wait_for_status(runner, run_id, "awaiting_approval")
    for path in (f"/api/runs/{run_id}", "/api/approvals"):
        body = (await api.get(path)).text
        assert secret not in body, path
        assert json.dumps(secret)[1:-1] not in body, path


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/api/runs", {"objective": "x", "unknown_field": "SECRET"}),
        ("post", "/api/runs", {"objective": "x", "options": "SECRET"}),
        ("post", "/api/runs", {"objective": "x", "llm": "SECRET"}),
        ("post", "/api/runs", {"objective": "x", "options": {"SECRET": 1}}),
        ("post", "/api/runs", {"objective": "x", "options": {"limits": {"SECRET": 1}}}),
        ("post", "/api/runs", {"objective": "x", "options": {"faults": {"SECRET": {"mode": "timeout"}}}}),
        ("get", "/api/runs?limit=SECRET", None),
        ("get", "/api/approvals?status=SECRET", None),
    ],
)
async def test_secrets_masked_in_validation_errors(api, monkeypatch, method, path, body):
    secret = "sk-live-echo-0102030405"
    monkeypatch.setattr(settings, "llm_api_key", secret)
    path, body = path.replace("SECRET", secret), json.loads(json.dumps(body).replace("SECRET", secret))
    response = await (api.post(path, json=body) if method == "post" else api.get(path))
    assert response.status_code == 422
    assert secret not in response.text and "***" in response.text


async def test_secrets_masked_in_decision_errors(api, runner, search, monkeypatch):
    secret = "sk-live-echo-0102030405"
    run_id, approval = await paused_run(api, runner)
    monkeypatch.setattr(settings, "llm_api_key", secret)
    url = decide_url(run_id, approval["id"])
    for body in (
        {"decision": "reject", "reason": " ", "args": {"note": secret}},
        {"decision": "reject", "reason": secret * 30},
    ):
        response = await api.post(url, json=body)
        assert response.status_code == 422 and secret not in response.text


async def test_usage_survives_null_token_counts(api, runner):
    run_id = await create(api)
    await wait_for_status(runner, run_id, "completed")
    await runner.tracer.emit(run_id, "llm", data={"prompt_tokens": None, "completion_tokens": None})
    assert (await api.get(f"/api/runs/{run_id}")).json()["usage"] == {"prompt_tokens": 0, "completion_tokens": 0}
