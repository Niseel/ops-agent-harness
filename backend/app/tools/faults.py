"""Per-run fault injection options (ADR 0012, spec: Fault injection).

A fault hits the first `times` attempts of its target in the run, counted
from the checkpointed attempt counters, so a resumed run behaves the same.
"""

from dataclasses import dataclass
from typing import Literal

from pydantic import Field, model_validator

from app.config import Strict


class ToolFault(Strict):
    mode: Literal["timeout", "error", "bad_output", "latency", "timeout_after_commit"]
    times: int = Field(1, ge=1)
    ms: int = Field(1000, ge=0)  # latency only


class LLMFault(Strict):
    mode: Literal["malformed", "timeout"]
    times: int = Field(1, ge=1)


class EmbeddingsFault(Strict):
    mode: Literal["error"]
    times: int = Field(1, ge=1)


class Faults(Strict):
    search_knowledge_base: ToolFault | None = None
    get_service_status: ToolFault | None = None
    create_incident: ToolFault | None = None
    llm: LLMFault | None = None
    embeddings: EmbeddingsFault | None = None

    @model_validator(mode="after")
    def _commit_fault_only_for_incidents(self) -> "Faults":
        for name in ("search_knowledge_base", "get_service_status"):
            fault = getattr(self, name)
            if fault is not None and fault.mode == "timeout_after_commit":
                raise ValueError(f"timeout_after_commit is only valid for create_incident, not {name}")
        return self

    def for_tool(self, name: str) -> ToolFault | None:
        return (
            getattr(self, name) if name in ("search_knowledge_base", "get_service_status", "create_incident") else None
        )


def hits(fault: ToolFault | LLMFault | EmbeddingsFault | None, attempts_before: int) -> bool:
    """True when the next attempt (after `attempts_before` earlier ones in the run) must fail."""
    return fault is not None and attempts_before < fault.times


@dataclass
class EmbedCounter:
    """The run's query-embedding attempts, for the `embeddings` fault. Starts from the checkpointed count."""

    fault: EmbeddingsFault | None
    attempts: int

    def next_fails(self) -> bool:
        """Called once per search attempt that would embed: True means this attempt is an injected failure."""
        fails = hits(self.fault, self.attempts)
        self.attempts += 1
        return fails
