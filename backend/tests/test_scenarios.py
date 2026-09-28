"""Scenario eval files and their grader, evals/check.sh (spec: Evaluation, scenario evals)."""

import copy
import json
import re
import shutil
import subprocess

import pytest

from app.api.runs import Decision
from app.config import ROOT
from app.harness import policy
from app.harness.state import FINAL_STATUSES
from app.tools.registry import TOOLS

EVALS = ROOT / "evals"
NAMES = {
    "approve-incident",
    "status-timeout-retry",
    "malformed-reply",
    "step-limit",
    "prompt-injection",
    "degraded-search",
    "timeout-after-commit",
}
REQUIRED = {"name", "objective", "llm", "decisions", "expect"}


def test_scenario_files_follow_the_format():
    paths = sorted(EVALS.glob("*.json"))
    assert {path.stem for path in paths} == NAMES
    for path in paths:
        scenario = json.loads(path.read_text())
        assert REQUIRED <= scenario.keys() <= REQUIRED | {"limits", "faults"}, path.name
        assert scenario["name"] == path.stem
        assert scenario["llm"] == "fake"
        policy.parse_options({k: scenario[k] for k in ("limits", "faults") if k in scenario}, allow_faults=True)
        for decision in scenario["decisions"]:
            Decision.model_validate(decision)
        expect = scenario["expect"]
        assert "status" in expect and expect.keys() <= {"status", "attempts", "incidents", "search_mode"}, path.name
        assert expect["status"] in FINAL_STATUSES
        assert set(expect.get("attempts", {})) <= set(TOOLS) | {"llm"}, path.name


# --- the grader -------------------------------------------------------------------------------

needs_jq = pytest.mark.skipif(shutil.which("jq") is None, reason="evals/check.sh needs jq")

SCENARIO = {
    "name": "grader",
    "objective": "payments-api is returning 5xx errors. Investigate and open an incident if needed.",
    "llm": "fake",
    "decisions": [{"decision": "approve"}],
    "expect": {
        "status": "completed",
        "attempts": {"create_incident": 1, "llm": 2},
        "incidents": 1,
        "search_mode": "sparse_only",
    },
}


def event(seq, kind, **fields):
    return {"seq": seq, "kind": kind, "tool": None, "status": None, "data": None, **fields}


def search(seq, mode):
    result = {"ok": True, "data": {"mode": mode, "results": []}}
    return event(seq, "tool", tool="search_knowledge_base", status="ok", data={"attempt": 1, "result": result})


RESULT = {
    "run_id": "r1",
    "status": "completed",
    "events": [
        event(1, "llm", status="tool_calls"),
        search(2, "sparse_only"),
        event(3, "tool", tool="create_incident", status="validation", data={"attempt": 0}),  # refused: not counted
        event(4, "approval", tool="create_incident", status="pending", data={}),
        event(5, "approval", tool="create_incident", status="approved", data={}),
        event(6, "tool", tool="create_incident", status="ok", data={"attempt": 1, "result": {"ok": True, "data": {}}}),
        event(7, "llm", status="final"),
        event(8, "done", status="completed"),
    ],
    "incidents": [{"id": "INC-0000000A", "run_id": "r1"}],
}


def grade(tmp_path, scenario, result):
    (tmp_path / "scenario.json").write_text(json.dumps(scenario))
    (tmp_path / "result.json").write_text(json.dumps(result))
    command = ["bash", str(EVALS / "check.sh"), str(tmp_path / "scenario.json"), str(tmp_path / "result.json")]
    return subprocess.run(command, capture_output=True, text=True)


@needs_jq
def test_check_sh_passes_a_matching_trace(tmp_path):
    graded = grade(tmp_path, SCENARIO, RESULT)
    assert graded.returncode == 0, graded.stdout + graded.stderr
    lines = graded.stdout.splitlines()[1:]
    assert len(lines) == 6 and all(line.split()[0] == "ok" for line in lines), graded.stdout


def events(result):
    return result["events"]


# Each change breaks exactly one check, named here.
MISMATCHES = {
    "another done status": ("status", lambda r: events(r)[-1].update(status="failed")),
    "a second done": ("status", lambda r: events(r).append(event(9, "done", status="completed"))),
    "one attempt more": (
        "attempts create_incident",
        lambda r: events(r).append(event(9, "tool", tool="create_incident", status="ok", data={"attempt": 2})),
    ),
    "one llm event less": ("attempts llm", lambda r: events(r).pop(0)),
    "a missing decision event": ("decisions", lambda r: events(r).pop(4)),
    "an extra decision event": ("decisions", lambda r: events(r).append(event(9, "approval", status="rejected"))),
    "another incident count": ("incidents", lambda r: r["incidents"].append({"id": "INC-0000000B", "run_id": "r1"})),
    "no incidents key": ("incidents", lambda r: r.pop("incidents")),
    "a hybrid search": ("search_mode", lambda r: events(r).__setitem__(1, search(2, "hybrid"))),
    "no search event": ("search_mode", lambda r: events(r).pop(1)),
}


@needs_jq
@pytest.mark.parametrize("change", MISMATCHES)
def test_check_sh_fails_on_each_mismatch(tmp_path, change):
    check, apply = MISMATCHES[change]
    result = copy.deepcopy(RESULT)
    apply(result)
    graded = grade(tmp_path, SCENARIO, result)
    assert graded.returncode == 1, graded.stdout + graded.stderr
    failed = {m[1] for m in re.finditer(r"^  FAIL (.+?): ", graded.stdout, re.MULTILINE)}
    assert failed == {check}, graded.stdout


@needs_jq
def test_check_sh_fails_on_a_result_that_is_not_json(tmp_path):
    (tmp_path / "scenario.json").write_text(json.dumps(SCENARIO))
    (tmp_path / "result.json").write_text("<html>Bad Gateway</html>")
    command = ["bash", str(EVALS / "check.sh"), str(tmp_path / "scenario.json"), str(tmp_path / "result.json")]
    graded = subprocess.run(command, capture_output=True, text=True)
    assert graded.returncode == 1
    assert "FAIL result:" in graded.stdout
