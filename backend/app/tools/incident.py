"""create_incident: a mock incident system backed by the incidents table (ADR 0009, ADR 0013)."""

from typing import Literal

from pydantic import Field

from app.config import Strict
from app.tools import Tool, ToolContext


class IncidentInput(Strict):
    title: str = Field(min_length=5, max_length=120)
    description: str = Field(min_length=10, max_length=2000)
    severity: Literal["SEV1", "SEV2", "SEV3", "SEV4"]


class IncidentOutput(Strict):
    incident_id: str = Field(pattern=r"^INC-[0-9A-F]{8}$")
    status: Literal["open"]
    created_at: str


async def create_incident(args: IncidentInput, ctx: ToolContext) -> dict:
    # A retry after a commit finds the row by its key instead of creating a second incident.
    row = await ctx.store.create_incident(idempotency_key=ctx.idempotency_key, run_id=ctx.run_id, **args.model_dump())
    return {"incident_id": row["id"], "status": row["status"], "created_at": row["created_at"]}


TOOL = Tool(
    name="create_incident",
    description=(
        "Open an incident. A person reviews every call and may approve, edit or reject it; "
        "the incident exists only after approval."
    ),
    input_model=IncidentInput,
    output_model=IncidentOutput,
    run=create_incident,
    requires_approval=True,
)
