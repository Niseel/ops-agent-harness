from typing import Annotated

from fastapi import APIRouter, Depends

from app.api import get_runner
from app.config import settings
from app.harness.runner import Runner
from app.tools.registry import TOOLS

router = APIRouter(prefix="/api", tags=["meta"])
RunnerDep = Annotated[Runner, Depends(get_runner)]
_INCIDENT_FIELDS = ("id", "run_id", "title", "description", "severity", "status", "created_at")


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "llm_default": settings.llm_default}


@router.get("/tools")
async def list_tools() -> list[dict]:
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_model.model_json_schema(),
            "requires_approval": tool.requires_approval,
        }
        for tool in TOOLS.values()
    ]


@router.get("/incidents")
async def list_incidents(runner: RunnerDep) -> list[dict]:
    # The spec's fields only: the idempotency key is internal.
    return [{key: incident[key] for key in _INCIDENT_FIELDS} for incident in await runner.store.list_incidents()]
