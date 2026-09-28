import json
import logging

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import log
from app.config import ROOT, Config, Settings, cfg
from app.main import app


def test_health():
    with TestClient(app) as client:  # the lifespan opens the runner that health reads
        assert client.get("/api/health").json()["status"] == "ok"


def test_config_yaml_loads_every_tool():
    assert set(cfg.tools) == {"search_knowledge_base", "get_service_status", "create_incident"}
    assert cfg.tool("unknown").max_attempts == 3  # default for tools without an entry


def test_relative_paths_resolve_from_repo_root(monkeypatch):
    monkeypatch.setenv("DB_PATH", "data/other.db")
    assert Settings().db_path == ROOT / "data" / "other.db"
    monkeypatch.setenv("DB_PATH", "/tmp/x.db")
    assert str(Settings().db_path) == "/tmp/x.db"


def test_config_rejects_unknown_keys():
    with pytest.raises(ValidationError):
        Config.model_validate({"limits": {"max_stepz": 3}})


def test_json_log_has_run_fields_and_masks_secrets(capsys):
    log.setup("INFO", "json", ["sk-secret-123"])
    logging.getLogger("t").info("calling with sk-secret-123", extra={"run_id": "r1", "tool": "x"})
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["msg"] == "calling with ***"
    assert (line["run_id"], line["tool"]) == ("r1", "x")
