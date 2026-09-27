"""get_service_status: a mock status API that reads data/services.json."""

import json
from typing import Literal

from pydantic import Field

from app.clock import ISO_PATTERN
from app.config import Strict, settings
from app.tools import Tool, ToolContext, ToolError


class StatusInput(Strict):
    service_name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,49}$", description="Service name, e.g. payments-api")


class StatusOutput(Strict):
    service: str
    status: Literal["operational", "degraded", "down"]
    latency_p95_ms: int = Field(ge=0)
    error_rate: float = Field(ge=0, le=1)
    updated_at: str = Field(pattern=ISO_PATTERN)


async def get_service_status(args: StatusInput, ctx: ToolContext) -> dict:
    # Read on every call: the file is small, and editing it changes the next run.
    for record in json.loads((settings.data_dir / "services.json").read_text(encoding="utf-8")):
        if record.get("service") == args.service_name:
            return record  # as is; the gateway checks it against StatusOutput
    raise ToolError("not_found", f"unknown service {args.service_name!r}")


TOOL = Tool(
    name="get_service_status",
    description="Current status of one service: operational, degraded or down, with p95 latency and error rate.",
    input_model=StatusInput,
    output_model=StatusOutput,
    run=get_service_status,
)
