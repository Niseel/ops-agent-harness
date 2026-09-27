import asyncio
import json
from dataclasses import replace

import pytest

from app.config import cfg
from app.harness import policy
from app.harness.policy import RunOptions, check_calls, parse_options, recursion_limit
from app.llm import fake
from app.tools import registry

STATUS = {"id": "c0", "name": "get_service_status", "args": {"service_name": "payments-api"}}
INCIDENT = {
    "id": "c1",
    "name": "create_incident",
    "args": {"title": "payments-api is degraded", "description": "p95 2400 ms, error rate 12%.", "severity": "SEV2"},
}


def status(service: str, id: str) -> dict:
    return {"id": id, "name": "get_service_status", "args": {"service_name": service}}


def check(calls, *, tool_calls=0, call_counts=None, incidents=0, **limits):
    return check_calls(
        calls,
        limits=cfg.limits.model_copy(update=limits),
        tool_calls=tool_calls,
        call_counts=call_counts or {},
        incidents=incidents,
    )


def refusals(checked):
    return [p["refusal"]["error"]["type"] if p["refusal"] else None for p in checked.pending]


def test_limits_clamped_to_config():
    options = parse_options(
        {"limits": {"max_steps": 100, "max_tool_calls": 2, "max_run_seconds": 9999}}, allow_faults=True
    )
    assert options.limits.max_steps == cfg.limits.max_steps  # asked above config.yaml
    assert options.limits.max_tool_calls == 2  # lowered
    assert options.limits.max_run_seconds == cfg.limits.max_run_seconds
    assert options.limits.max_repairs == cfg.limits.max_repairs  # not asked
    assert parse_options(None, allow_faults=False).limits == cfg.limits
    assert parse_options({}, allow_faults=False) == RunOptions(limits=cfg.limits)


@pytest.mark.parametrize("name", list(type(cfg.limits).model_fields))
def test_every_limit_key_accepted_and_clamped(name):
    below = 1  # every limit's floor
    options = parse_options({"limits": {name: below}}, allow_faults=True)
    assert getattr(options.limits, name) == below
    above = getattr(cfg.limits, name) + 1
    assert getattr(parse_options({"limits": {name: above}}, allow_faults=True).limits, name) == getattr(
        cfg.limits, name
    )
    for other in type(cfg.limits).model_fields:
        if other != name:
            assert getattr(options.limits, other) == getattr(cfg.limits, other)


def test_limit_equal_to_config_is_kept():
    asked = cfg.limits.max_tool_calls
    assert parse_options({"limits": {"max_tool_calls": asked}}, allow_faults=True).limits.max_tool_calls == asked


def test_options_limits_null_keeps_config():
    assert parse_options({"limits": None}, allow_faults=True).limits == cfg.limits


def test_evaluate_true_kept():
    assert parse_options({"evaluate": True}, allow_faults=True).evaluate is True


def test_options_round_trip_through_json():
    options = parse_options(
        {
            "limits": {"max_steps": 2},
            "faults": {"get_service_status": {"mode": "timeout", "times": 2}},
            "evaluate": False,
        },
        allow_faults=True,
    )
    stored = options.model_dump(mode="json")
    assert stored["limits"]["max_steps"] == 2 and stored["evaluate"] is False
    assert stored["faults"]["get_service_status"] == {"mode": "timeout", "times": 2, "ms": 1000}
    assert RunOptions.model_validate(stored) == options


@pytest.mark.parametrize(
    ("raw", "allow_faults"),
    [
        ({"limits": {"max_steps": 0}}, True),
        ({"limits": {"max_tool_calls": -1}}, True),
        ({"limits": {"max_run_seconds": 0.5}}, True),
        ({"limits": {"max_steps": "5"}}, True),
        ({"limits": {"max_steps": True}}, True),
        ({"limits": {"max_steps": 2.5}}, True),
        ({"limits": {"max_tokens": 5}}, True),
        ({"faults": {"unknown_tool": {"mode": "timeout"}}}, True),
        ({"faults": {"get_service_status": {"mode": "explode"}}}, True),
        ({"faults": {"get_service_status": {"mode": "timeout_after_commit"}}}, True),
        ({"faults": {"search_knowledge_base": {"mode": "timeout_after_commit"}}}, True),
        ({"faults": {"get_service_status": {"mode": "timeout"}}}, False),
        ({"faults": {"llm": {"mode": "malformed"}}}, False),
        ({"evaluate": "yes"}, True),
        ({"llm": "fake"}, True),
        ([], True),
    ],
)
def test_invalid_options_rejected(raw, allow_faults):
    with pytest.raises(ValueError):
        parse_options(raw, allow_faults=allow_faults)


@pytest.mark.parametrize("faults", [None, {}, {"llm": None}])
def test_no_faults_accepted_when_fault_injection_is_off(faults):
    assert parse_options({"faults": faults}, allow_faults=False).faults.model_dump(exclude_none=True) == {}


def test_recursion_limit():
    assert recursion_limit(cfg.limits.model_copy(update={"max_steps": 2})) == 13
    assert policy.recursion_limit(cfg.limits.model_copy(update={"max_steps": 8})) == 37


def test_allowed_calls_counted_without_changing_the_input():
    counts = {"x": 1}
    checked = check([STATUS, status("orders-db", "c1")], call_counts=counts)
    assert refusals(checked) == [None, None] and checked.error is None
    assert checked.call_counts == {
        "x": 1,
        'get_service_status:{"service_name": "payments-api"}': 1,
        'get_service_status:{"service_name": "orders-db"}': 1,
    }
    assert counts == {"x": 1}
    assert [p["id"] for p in checked.pending] == ["c0", "c1"]


def test_max_tool_calls_blocks_extra_calls_in_one_reply():
    calls = [status(s, f"c{i}") for i, s in enumerate(["payments-api", "orders-db", "auth-service"])]
    checked = check(calls, tool_calls=0, max_tool_calls=2)
    assert refusals(checked) == [None, None, "blocked"]
    assert checked.error == "max_tool_calls"
    assert "tool call limit reached (2 per run)" in checked.pending[2]["refusal"]["error"]["message"]


def test_exact_max_tool_calls_is_not_an_error():
    checked = check([STATUS], tool_calls=1, max_tool_calls=2)
    assert refusals(checked) == [None] and checked.error is None


def test_repeat_guard_blocks_call_at_cap():
    key = policy.call_key(STATUS)
    checked = check([STATUS], call_counts={key: 2}, max_repeat_calls=2)
    assert refusals(checked) == ["blocked"] and checked.error is None
    assert checked.call_counts[key] == 2  # a blocked call is not counted


def test_repeat_call_counts_calls_in_same_reply():
    same_args_other_order = {**STATUS, "id": "c1", "args": dict(reversed(list(STATUS["args"].items())))}
    checked = check([STATUS, same_args_other_order], max_repeat_calls=1)
    assert refusals(checked) == [None, "blocked"]


def test_incident_cap_counts_calls_in_same_reply():
    second = {**INCIDENT, "id": "c2", "args": {**INCIDENT["args"], "severity": "SEV1"}}
    checked = check([INCIDENT, second], max_incidents_per_run=1)
    assert refusals(checked) == [None, "blocked"]
    assert "incident limit reached (1 per run)" in checked.pending[1]["refusal"]["error"]["message"]
    assert refusals(check([INCIDENT], incidents=1, max_incidents_per_run=1)) == ["blocked"]


@pytest.mark.parametrize(
    "call",
    [
        {**STATUS, "args": {"service_name": "Payments API"}},
        {**STATUS, "args": ["payments-api"]},
        {**INCIDENT, "args": {**INCIDENT["args"], "severity": "SEV9"}},
        {"id": "c9", "name": "delete_database", "args": {}},
    ],
)
def test_validation_comes_before_limits(call):
    # The call limit is already reached, but the first failing check wins, so no run error either.
    checked = check([call], tool_calls=2, max_tool_calls=2)
    assert refusals(checked) == ["validation"] and checked.error is None


def test_invalid_incident_args_are_refused_before_approval():
    checked = check([{**INCIDENT, "args": {**INCIDENT["args"], "extra": 1}}])
    [pending] = checked.pending
    assert pending["refusal"]["error"]["type"] == "validation"
    assert checked.call_counts == {}


def test_empty_call_list():
    checked = check([], call_counts={"x": 1})
    assert checked.pending == [] and checked.error is None
    assert checked.call_counts == {"x": 1}


def test_call_key_stable_for_nested_and_unicode_args():
    call = {"id": "c0", "name": "get_service_status", "args": {"service_name": "café", "tags": {"a": 1, "b": [1, 2]}}}
    same_built_differently = {
        "id": "c1",
        "name": "get_service_status",
        "args": {"tags": {"b": [1, 2], "a": 1}, "service_name": "café"},
    }
    assert policy.call_key(call) == policy.call_key(same_built_differently)


def test_check_calls_with_call_counts_from_previous_reply():
    # One earlier allowed call is below a cap of 2: this one runs and brings the count to the cap.
    key = policy.call_key(STATUS)
    checked = check([STATUS], call_counts={key: 1}, max_repeat_calls=2)
    assert refusals(checked) == [None]
    assert checked.call_counts[key] == 2


def test_incident_cap_lowered_by_options_is_enforced():
    options = parse_options({"limits": {"max_incidents_per_run": 1}}, allow_faults=True)
    checked = check_calls([INCIDENT], limits=options.limits, tool_calls=0, call_counts={}, incidents=1)
    assert refusals(checked) == ["blocked"]


def test_max_tool_calls_wins_over_repeat_check_order():
    # tool_calls already at the cap and the repeat cap already reached: max_tool_calls wins (checked first).
    key = policy.call_key(STATUS)
    checked = check([STATUS], call_counts={key: 2}, tool_calls=2, max_tool_calls=2, max_repeat_calls=2)
    assert refusals(checked) == ["blocked"]
    assert checked.error == "max_tool_calls"
    assert "tool call limit reached" in checked.pending[0]["refusal"]["error"]["message"]


def test_repeat_wins_over_incident_cap_check_order():
    # Same call already at its repeat cap and the incident cap already reached: repeat wins (checked first).
    key = policy.call_key(INCIDENT)
    checked = check([INCIDENT], call_counts={key: 1}, incidents=1, max_repeat_calls=1, max_incidents_per_run=1)
    assert refusals(checked) == ["blocked"]
    assert "already allowed" in checked.pending[0]["refusal"]["error"]["message"]


def test_error_stays_max_tool_calls_when_a_later_call_is_blocked_for_another_reason():
    # tool_calls is already at the cap, so the first call is blocked by it; a later call with
    # invalid args gets "validation" instead, but the run error stays "max_tool_calls".
    invalid = {**STATUS, "id": "c1", "args": {"service_name": "Payments API"}}
    checked = check([STATUS, invalid], tool_calls=2, max_tool_calls=2)
    assert refusals(checked) == ["blocked", "validation"]
    assert checked.error == "max_tool_calls"


# --- run level (T6) ---------------------------------------------------------------------------


def status_calls(*services, prefix="c"):
    return fake.calls(*[("get_service_status", {"service_name": s}, f"{prefix}{i}") for i, s in enumerate(services)])


async def start(runner, llm, **limits):
    run = await runner.create_run("Check the services", options={"limits": limits})
    status = await runner.run_segment(run["id"], llm_client=llm)
    return status, await runner.store.get_run(run["id"]), await runner.get_state(run["id"])


def results(state):
    return [json.loads(m["content"]) for m in state["messages"] if m["role"] == "tool"]


async def test_limits_stored_on_the_run(runner):
    run = await runner.create_run("Check payments-api", options={"limits": {"max_steps": 100, "max_tool_calls": 3}})
    stored = (await runner.store.get_run(run["id"]))["options"]
    assert stored["limits"] == {**cfg.limits.model_dump(), "max_tool_calls": 3}
    assert stored["evaluate"] is False  # resolved at create_run; tests have no judge


async def test_max_steps_stops_run(runner):
    llm = fake.ScriptedLLM([status_calls("payments-api"), status_calls("orders-db", prefix="d")])
    status, row, state = await start(runner, llm, max_steps=2)
    assert (status, row["error"], row["steps"], len(llm.seen)) == ("limit_exceeded", "max_steps", 2, 2)


async def test_max_tool_calls_blocks_extra_call(runner):
    llm = fake.ScriptedLLM([status_calls("payments-api", "orders-db", "auth-service")])
    status, row, state = await start(runner, llm, max_tool_calls=2)
    assert (status, row["error"], row["tool_calls"]) == ("limit_exceeded", "max_tool_calls", 2)
    assert [r["ok"] for r in results(state)[:2]] == [True, True]
    assert results(state)[2]["error"]["type"] == "blocked"
    assert len(llm.seen) == 1  # the guard ends the run before another LLM turn


async def test_exact_max_tool_calls_then_answer_completes(runner):
    llm = fake.ScriptedLLM([status_calls("payments-api", "orders-db"), fake.final("done")])
    status, row, _ = await start(runner, llm, max_tool_calls=2)
    assert (status, row["error"], row["tool_calls"]) == ("completed", None, 2)


async def test_repeat_call_blocked(runner):
    llm = fake.ScriptedLLM(
        [status_calls("payments-api"), status_calls("payments-api"), status_calls("payments-api"), fake.final("done")]
    )
    status, row, state = await start(runner, llm, max_repeat_calls=2)
    assert status == "completed" and row["tool_calls"] == 2
    assert [r["ok"] for r in results(state)] == [True, True, False]
    assert results(state)[2]["error"]["type"] == "blocked"


async def test_segment_timeout_ends_timed_out(runner, monkeypatch):
    async def slow(args, ctx):
        await asyncio.sleep(2)
        return {}

    tool = registry.TOOLS["get_service_status"]
    monkeypatch.setitem(registry.TOOLS, tool.name, replace(tool, run=slow))
    monkeypatch.setattr(cfg.tools["get_service_status"], "timeout_s", 5)  # the segment limit fires first
    status, row, state = await start(runner, fake.ScriptedLLM([status_calls("payments-api")]), max_run_seconds=1)
    assert (status, row["error"], row["steps"]) == ("timed_out", "max_run_seconds", 1)
    done = [e for e in await runner.store.list_events(row["id"]) if e["kind"] == "done"]
    assert [(e["status"], e["attention"]) for e in done] == [("timed_out", "error")]


async def test_recursion_limit_ends_limit_exceeded(runner, monkeypatch):
    monkeypatch.setattr(policy, "recursion_limit", lambda limits: 3)
    llm = fake.ScriptedLLM([status_calls("payments-api"), fake.final("done")])
    status, row, _ = await start(runner, llm)
    assert (status, row["error"]) == ("limit_exceeded", "recursion_limit")
    done = [e for e in await runner.store.list_events(row["id"]) if e["kind"] == "done"]
    assert len(done) == 1
