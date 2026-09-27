import random

import pytest
from pydantic import ValidationError

from app.harness.retry import backoff
from app.tools.faults import Faults, hits


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
