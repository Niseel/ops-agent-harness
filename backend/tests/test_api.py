import asyncio
import json
from types import SimpleNamespace

import fastapi.routing
import httpx2
import pytest
from conftest import FakeEmbedder, FakeJudge, wait_for_status

from app.api import eval as eval_api
from app.api import get_runner, runs
from app.clock import now_iso
from app.config import cfg, settings
from app.eval import golden, metrics
from app.eval.golden import MODES, load_golden
from app.kb import qdrant
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


# --- T5: live events, evaluation and health ------------------------------------------------


def parse_sse(text: str) -> list[dict]:
    """One dict per message: `id`, `event`, `data` (JSON) and `comment` (a `:` line)."""
    messages = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        message = {}
        for line in block.splitlines():
            field, _, value = line.partition(":")
            value = value.removeprefix(" ")
            if field == "":
                message["comment"] = value
            elif field == "data":
                message["data"] = json.loads(value)
            elif field in ("id", "event"):
                message[field] = value
        if message:
            messages.append(message)
    return messages


async def wait_subscribed(runner, run_id) -> None:
    for _ in range(250):
        if runner.tracer._subscribers.get(run_id):
            return
        await asyncio.sleep(0.02)
    pytest.fail("the event stream never subscribed")


async def open_stream(api, runner, run_id, **headers) -> asyncio.Task:
    """ASGITransport returns the response only when the stream ends, so the request runs as a task."""
    task = asyncio.create_task(api.get(f"/api/runs/{run_id}/events", headers=headers))
    await wait_subscribed(runner, run_id)
    return task


async def ended(task: asyncio.Task) -> httpx2.Response:
    async with asyncio.timeout(5):
        return await task


async def audits(runner, action) -> list[dict]:
    rows = await runner.store._all("SELECT data_json FROM events WHERE run_id IS NULL")
    return [data for row in rows if (data := json.loads(row["data_json"]))["action"] == action]


async def test_sse_replays_then_streams(api, runner, search):
    run_id, approval = await paused_run(api, runner)
    stream = await open_stream(api, runner, run_id)
    await api.post(decide_url(run_id, approval["id"]), json={"decision": "approve"})  # the rest comes live
    response = await ended(stream)
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    messages = [m for m in parse_sse(response.text) if "data" in m]
    trace = (await api.get(f"/api/runs/{run_id}/trace")).json()["events"]
    assert [m["data"] for m in messages] == trace  # the same dicts as the trace, `done` last
    assert [m["id"] for m in messages] == [str(e["seq"]) for e in trace]
    assert all("event" not in m for m in messages)  # no event name: EventSource.onmessage gets them all
    kinds = [e["kind"] for e in trace]
    assert "approval" in kinds and kinds[-1] == "done"


async def test_sse_resumes_after_last_event_id(api, runner, search):
    run_id = await create(api, "Why is payments-api slow?")
    await wait_for_status(runner, run_id, "completed")
    events = (await api.get(f"/api/runs/{run_id}/trace")).json()["events"]
    url = f"/api/runs/{run_id}/events"
    middle = events[2]["seq"]
    response = await api.get(url, headers={"Last-Event-ID": str(middle)})
    assert [int(m["id"]) for m in parse_sse(response.text)] == [e["seq"] for e in events if e["seq"] > middle]
    at_done = await api.get(url, headers={"Last-Event-ID": str(events[-1]["seq"])})
    assert at_done.status_code == 200 and parse_sse(at_done.text) == []  # the client already has `done`
    for bad in ("abc", "-1", str(2**63)):  # beyond SQLite's integer range: 422, not a broken stream
        assert (await api.get(url, headers={"Last-Event-ID": bad})).status_code == 422


async def test_sse_closes_after_done(api, runner, search, monkeypatch):
    monkeypatch.setattr(metrics, "get_judge", lambda: FakeJudge())
    run_id = await create(api, "Why is payments-api slow?", options={"evaluate": True})
    await wait_for_status(runner, run_id, "completed")
    for _ in range(250):  # online evaluation writes its events after `done`
        kinds = [e["kind"] for e in (await api.get(f"/api/runs/{run_id}/trace")).json()["events"]]
        if "eval" in kinds:
            break
        await asyncio.sleep(0.02)
    assert kinds.index("eval") > kinds.index("done")
    streamed = [m["data"]["kind"] for m in parse_sse((await api.get(f"/api/runs/{run_id}/events")).text)]
    assert streamed[-1] == "done" and "eval" not in streamed


async def test_sse_sends_keep_alive(api, runner, search, monkeypatch):
    monkeypatch.setattr(fastapi.routing, "_PING_INTERVAL", 0.05)
    run_id, approval = await paused_run(api, runner)
    stream = await open_stream(api, runner, run_id)
    await asyncio.sleep(0.3)  # the paused run keeps its stream open
    await api.post(decide_url(run_id, approval["id"]), json={"decision": "approve"})
    messages = parse_sse((await ended(stream)).text)
    assert {"comment": "ping"} in messages and messages[-1]["data"]["kind"] == "done"


async def test_sse_unknown_run_returns_404(api):
    response = await api.get("/api/runs/nope/events")
    assert response.status_code == 404 and response.json() == {"detail": "run nope not found"}


async def test_sse_ends_on_final_row_without_done(api, runner, search, monkeypatch):
    # A crash between the run row's final write and the `done` event: the stream must not wait forever.
    monkeypatch.setattr(runs, "GRACE_S", 0.05)  # how long it waits for a `done` that never comes
    crashed = await runner.create_run("Check payments-api")
    await runner.store.update_run(crashed["id"], status="failed")
    response = await ended(asyncio.create_task(api.get(f"/api/runs/{crashed['id']}/events")))
    assert response.status_code == 200 and parse_sse(response.text) == []
    # The same while the stream is open: noticed when the queue has been idle.
    monkeypatch.setattr(runs, "IDLE_S", 0.05)
    run_id, _ = await paused_run(api, runner)
    stream = await open_stream(api, runner, run_id)
    await runner.store.update_run(run_id, status="failed")
    kinds = [m["data"]["kind"] for m in parse_sse((await ended(stream)).text) if "data" in m]
    assert "done" not in kinds and kinds[-1] == "approval"


async def test_sse_opened_while_a_cancel_finishes_gets_done(api, runner, search, monkeypatch):
    # A cancel writes the final row first; its audit event and `done` come after the segment stops (reviewer T5).
    run_id, _ = await paused_run(api, runner)
    real = runner.get_state

    async def slow(rid):
        await asyncio.sleep(0.3)  # stands in for the segment teardown and the lock wait
        return await real(rid)

    monkeypatch.setattr(runner, "get_state", slow)
    cancel = asyncio.create_task(api.post(f"/api/runs/{run_id}/cancel"))
    await wait_for_status(runner, run_id, "cancelled")
    response = await ended(asyncio.create_task(api.get(f"/api/runs/{run_id}/events")))
    assert (await cancel).status_code == 200
    kinds = [m["data"]["kind"] for m in parse_sse(response.text) if "data" in m]
    assert kinds[-2:] == ["log", "done"]


async def test_sse_sends_stored_event_that_was_never_published(api, runner, search):
    run_id, approval = await paused_run(api, runner)
    stream = await open_stream(api, runner, run_id)
    # A segment cancelled between the tracer's insert and its publish leaves an event no subscriber got.
    seq = await runner.store.insert_event(
        {"run_id": run_id, "t_ms": 1, "kind": "log", "msg": "stored only", "created_at": now_iso()}
    )
    await api.post(decide_url(run_id, approval["id"]), json={"decision": "approve"})
    messages = parse_sse((await ended(stream)).text)
    assert str(seq) in [m["id"] for m in messages]
    ids = [int(m["id"]) for m in messages if "id" in m]
    assert ids == sorted(set(ids)) and messages[-1]["data"]["kind"] == "done"


async def test_sse_stays_open_on_interrupted_run(api, runner, monkeypatch):
    monkeypatch.setattr(runs, "IDLE_S", 0.05)
    # No live segment: the row is set directly, as `recover()` would leave it after a crash mid-run.
    run = await runner.create_run("Check payments-api")
    await runner.store.update_run(run["id"], status="interrupted")
    stream = await open_stream(api, runner, run["id"])
    await asyncio.sleep(0.2)  # several idle windows; `interrupted` is not final, so the stream stays open
    assert not stream.done()
    stream.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stream


async def test_eval_endpoint_streams_report(api, runner, kb, monkeypatch):
    monkeypatch.setattr(metrics, "get_judge", lambda: FakeJudge())
    response = await api.post("/api/eval/kb", json={"modes": ["sparse", "hybrid", "sparse"]})
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    messages = parse_sse(response.text)
    total = 2 * len(load_golden())  # duplicates dropped
    progress = [m["data"] for m in messages if m.get("event") == "progress"]
    assert progress == [{"done": done, "total": total} for done in range(1, total + 1)]
    assert messages[-1]["event"] == "report"
    report = messages[-1]["data"]
    assert report["config"]["modes"] == ["sparse", "hybrid"]
    assert report["summary"]["hybrid"]["context_precision"] == 0.9
    latest = (await api.get("/api/eval/kb/latest")).json()
    assert (latest["id"], latest["summary"]) == (report["id"], report["summary"])


async def test_eval_start_is_audited(api, runner, kb, monkeypatch):
    monkeypatch.setattr(metrics, "get_judge", lambda: FakeJudge(reachable=False))
    response = await api.post("/api/eval/kb")  # no body: every mode
    assert parse_sse(response.text)[-1]["data"]["config"]["modes"] == list(MODES)
    [audit] = await audits(runner, "start_eval")
    assert audit == {"actor": "anonymous", "action": "start_eval", "entity_id": cfg.eval.golden_set}


async def test_eval_without_kb_returns_503(api, runner):
    response = await api.post("/api/eval/kb", json={"modes": ["hybrid"]})
    assert response.status_code == 503 and response.json() == {"detail": "knowledge base unavailable"}
    assert await audits(runner, "start_eval") == []
    assert await runner.store.latest_eval_report() is None


async def test_latest_report_404_when_none(api):
    response = await api.get("/api/eval/kb/latest")
    assert response.status_code == 404 and response.json() == {"detail": "no evaluation report yet"}


@pytest.mark.parametrize("body", [{"modes": ["fuzzy"]}, {"modes": []}, {"modes": "hybrid"}, {"extra": 1}])
async def test_eval_body_rejects_unknown_mode(api, runner, kb, body):
    assert (await api.post("/api/eval/kb", json=body)).status_code == 422
    assert await audits(runner, "start_eval") == []


async def test_eval_disconnect_stops_without_report(runner, kb, monkeypatch):
    class GatedJudge(FakeJudge):
        """Scores the first question, then waits forever on the second."""

        def __init__(self) -> None:
            super().__init__()
            self.waiting, self.cancelled = asyncio.Event(), False

        async def context_precision(self, question, reference, contexts):
            if self.calls:
                self.waiting.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.cancelled = True
                    raise
            return await super().context_precision(question, reference, contexts)

    judge = GatedJudge()
    monkeypatch.setattr(metrics, "get_judge", lambda: judge)
    # The route's generator, driven directly: ASGITransport cannot drop a connection mid-stream.
    stream = eval_api.eval_kb(kb=kb, runner=runner, body=None)
    first = await anext(stream)
    assert first.event == "progress" and first.data["done"] == 1
    await judge.waiting.wait()
    await stream.aclose()  # what FastAPI does when the client leaves
    for _ in range(50):
        if judge.cancelled:
            break
        await asyncio.sleep(0.01)
    assert judge.cancelled and await runner.store.latest_eval_report() is None


async def test_eval_job_error_sends_error_event(api, runner, kb, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("golden set exploded")

    monkeypatch.setattr(golden, "run_golden", boom)
    response = await api.post("/api/eval/kb", json={"modes": ["hybrid"]})
    assert response.status_code == 200
    messages = parse_sse(response.text)
    assert messages[-1]["event"] == "error" and messages[-1]["data"] == {"detail": "evaluation failed"}
    assert "report" not in {m.get("event") for m in messages}
    assert await runner.store.latest_eval_report() is None


async def test_eval_report_masks_secrets(api, runner, kb, monkeypatch):
    # The stream skips MaskedJSONResponse (eval.py's own note); this proves its own mask() call works.
    monkeypatch.setattr(runner.tracer, "secrets", ["leak-me"])

    async def fake_run(kb, judge, store, *, modes, progress=None, golden=None):
        report = {
            "id": "r1",
            "created_at": now_iso(),
            "models": {},
            "config": {},
            "summary": {},
            "rows": [],
            "note": "leak-me is secret",
        }
        await store.insert_eval_report(report)
        if progress:
            await progress(1, 1)
        return report

    monkeypatch.setattr(golden, "run_golden", fake_run)
    response = await api.post("/api/eval/kb", json={"modes": ["hybrid"]})
    assert "leak-me" not in response.text
    messages = parse_sse(response.text)
    assert messages[-1]["data"]["note"] == "*** is secret"
    stored = await runner.store.latest_eval_report()
    assert stored["id"] == "r1"  # the report is stored as returned; only the stream response is masked


async def test_health_reports_kb_mode(api, runner, kb, monkeypatch):
    body = (await api.get("/api/health")).json()
    assert body == {
        "status": "ok",
        "llm_default": settings.llm_default,
        "db": {"ok": True},
        "llm": {"model": settings.llm_model, "reachable": False},  # no_real_llm
        "embeddings": {"model": "fake-embed", "reachable": True},
        "qdrant": {"reachable": True},
        "judge": {"model": "fake-judge", "reachable": False},  # no_real_judge
        "kb": {"mode": "hybrid"},
    }
    monkeypatch.setattr(kb, "embedder", FakeEmbedder(fail=True))
    body = (await api.get("/api/health")).json()
    assert body["kb"] == {"mode": "sparse_only"} and body["embeddings"]["reachable"] is False

    def no_kb():
        raise RuntimeError("qdrant is down")

    monkeypatch.setattr(qdrant, "get_kb", no_kb)
    body = (await api.get("/api/health")).json()
    assert body["kb"] == {"mode": "unavailable"} and body["qdrant"] == {"reachable": False}
    assert body["embeddings"] == {"model": settings.embed_model, "reachable": False}
    assert body["status"] == "ok"  # only the database makes it degraded


async def test_health_degraded_and_probes_capped(api, runner, monkeypatch):
    async def down() -> bool:
        return False

    async def hangs() -> bool:
        await asyncio.sleep(30)
        return True

    monkeypatch.setattr(runner.store, "ping", down)
    monkeypatch.setattr(runner, "llm_reachable", hangs)
    async with asyncio.timeout(4):  # each probe waits at most 2 s, and they run together
        response = await api.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded" and body["db"] == {"ok": False} and body["llm"]["reachable"] is False


async def test_health_body_has_no_secrets(api, runner, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-health-secret-1")
    assert "sk-health-secret-1" not in (await api.get("/api/health")).text
