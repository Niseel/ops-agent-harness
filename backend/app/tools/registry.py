"""The tools the harness allows. A tool that is not in TOOLS never runs.

Timeouts and attempts come from `cfg.tool(name)` when a call runs, so they are
not stored here.
"""

from app.tools import Tool, incident, kb, status

TOOLS: dict[str, Tool] = {t.name: t for t in (kb.TOOL, status.TOOL, incident.TOOL)}


def openai_tools() -> list[dict]:
    """Tool definitions for the LLM: the input models as JSON Schema."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_model.model_json_schema(),
            },
        }
        for t in TOOLS.values()
    ]
