"""What every tool is made of (spec: Tools). The tools themselves are in their own modules;
`registry.TOOLS` is the list the harness allows.

These types live here, not in the registry, because each tool module needs them
and the registry imports every tool module.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from app.harness.state import ErrorType
from app.harness.store import Store
from app.harness.tracer import Tracer
from app.tools.faults import EmbedCounter


class ToolError(Exception):
    """A tool's own failure; the gateway turns it into an error envelope of this type."""

    def __init__(self, type: ErrorType, message: str) -> None:
        super().__init__(message)
        self.type = type
        self.message = message


@dataclass(frozen=True)
class ToolContext:
    run_id: str
    tool_call_id: str
    store: Store
    tracer: Tracer
    embed: EmbedCounter | None = None  # only the search tool reads it

    @property
    def idempotency_key(self) -> str:
        # Hidden from the LLM: it is never part of a tool's input model.
        return f"{self.run_id}:{self.tool_call_id}"


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    run: Callable[[Any, ToolContext], Awaitable[Any]]  # (validated input, context) -> raw output
    requires_approval: bool = False
