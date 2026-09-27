import logging

import pytest

from app.config import cfg
from app.harness.store import Store
from app.harness.tracer import Tracer

SECRET = "sk-test-secret-123"


@pytest.fixture
async def store(tmp_path):
    # A file, never :memory:, because the checkpointer opens a second connection to it.
    s = await Store.open(tmp_path / "harness.db")
    yield s
    await s.close()


@pytest.fixture
def tracer(store):
    return Tracer(store, [SECRET])


@pytest.fixture(autouse=True)
def restore_logging():
    """Tests may call log.setup(); put the root handlers back afterwards."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


@pytest.fixture(autouse=True)
def zero_retry_delay(monkeypatch):
    monkeypatch.setattr(cfg.retry, "base_delay_s", 0.0)
    monkeypatch.setattr(cfg.retry, "max_delay_s", 0.0)


@pytest.fixture
def short_timeouts(monkeypatch):
    """Tool and LLM timeouts of 0.05 s, for fault tests that wait for a real timeout."""
    for tool in cfg.tools.values():
        monkeypatch.setattr(tool, "timeout_s", 0.05)
    monkeypatch.setattr(cfg.llm, "timeout_s", 0.05)
