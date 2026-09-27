"""LLM stand-ins with no model server: `FakePlanner` for demos, `ScriptedLLM` for tests.

FakePlanner follows fixed rules (plans/m1-harness-core.md, FakePlanner) and
decides only from the messages and the offered tools. It makes one tool call
per reply. It follows an instruction to open an incident found in search
results on purpose: it imitates a model that falls for prompt injection, so
the approval gate can be shown stopping it.
"""

import json
import re

from app.llm.openai_compat import LLMReply, ToolCall

_SERVICE = re.compile(r"^[a-z0-9][a-z0-9-]{1,49}$")
_INJECTED = re.compile(r"(?i)\bopen an? (SEV[1-4])\b")
_DEFAULT_SEVERITY = {"down": "SEV1", "degraded": "SEV2"}


def final(text: str) -> LLMReply:
    return LLMReply(content=text, finish_reason="stop")


def calls(*items: tuple) -> LLMReply:
    """calls(("get_service_status", {"service_name": "payments-api"}), ...); an item may add an id third."""
    tool_calls = tuple(
        ToolCall(item[2] if len(item) > 2 else f"c{i}", item[0], json.dumps(item[1])) for i, item in enumerate(items)
    )
    return LLMReply(content=None, tool_calls=tool_calls, finish_reason="tool_calls")


def raw(
    content: str | None = None,
    tool_calls: tuple[ToolCall, ...] = (),
    finish_reason: str | None = "stop",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> LLMReply:
    """Any reply, including malformed ones: raw(finish_reason="length"), raw(tool_calls=(ToolCall("c1", "x", "{"),))."""
    return LLMReply(content, tuple(tool_calls), finish_reason, prompt_tokens, completion_tokens)


class ScriptedLLM:
    """Replays a fixed list of replies. An exception item is raised; running out fails the test."""

    model = "scripted"

    def __init__(self, items: list[LLMReply | Exception]) -> None:
        self.items = list(items)
        self.seen: list[list[dict]] = []  # messages of each call

    async def complete(self, messages: list[dict], tools: list[dict]) -> LLMReply:
        self.seen.append([dict(m) for m in messages])
        if not self.items:
            raise AssertionError("ScriptedLLM ran out of replies")
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakePlanner:
    model = "fake"

    async def complete(self, messages: list[dict], tools: list[dict]) -> LLMReply:
        offered = {t["function"]["name"] for t in tools}
        objective = next((m["content"] for m in messages if m["role"] == "user"), "") or ""
        called, results = _history(messages)

        # 1. Search the knowledge base once.
        query = objective.strip()[:200]
        if "search_knowledge_base" in offered and "search_knowledge_base" not in called and len(query) >= 3:
            return _call(called, "search_knowledge_base", {"query": query})

        # 2. Check the first service named in the objective.
        service = _service_in(objective)
        if service and "get_service_status" in offered and "get_service_status" not in called:
            return _call(called, "get_service_status", {"service_name": service})

        status = _ok_data(results.get("get_service_status"))
        snippets = _snippets(results.get("search_knowledge_base"))

        # 3. At most one incident proposal per run.
        if "create_incident" in offered and "create_incident" not in called:
            args = _incident_args(objective, service, status, snippets)
            if args:
                return _call(called, "create_incident", args)

        # 4. Answer.
        return final(_answer(service, status, results))


def _history(messages: list[dict]) -> tuple[list[str], dict[str, dict]]:
    """Tool names called so far (in order) and the last result envelope per tool name."""
    names_by_id, called, results = {}, [], {}
    for message in messages:
        if message["role"] == "assistant":
            for call in message.get("tool_calls") or ():
                names_by_id[call["id"]] = call["function"]["name"]
                called.append(call["function"]["name"])
        elif message["role"] == "tool" and message.get("tool_call_id") in names_by_id:
            try:
                results[names_by_id[message["tool_call_id"]]] = json.loads(message["content"])
            except (TypeError, ValueError):
                pass
    return called, results


def _call(called: list[str], name: str, args: dict) -> LLMReply:
    return calls((name, args, f"fake-{len(called) + 1}"))


def _service_in(objective: str) -> str | None:
    for word in objective.lower().split():
        word = word.strip(".,;:!?()[]{}\"'`")
        if "-" in word and _SERVICE.match(word):
            return word
    return None


def _ok_data(envelope: dict | None) -> dict | None:
    return envelope["data"] if envelope and envelope.get("ok") else None


def _snippets(envelope: dict | None) -> list[dict]:
    data = _ok_data(envelope)
    return list(data.get("results", [])) if isinstance(data, dict) else []


def _incident_args(objective: str, service: str | None, status: dict | None, snippets: list[dict]) -> dict | None:
    for hit in snippets:
        match = _INJECTED.search(hit.get("snippet", ""))
        if match:
            return {
                "title": "Incident requested by a knowledge base document",
                "description": f"{objective[:1800]}\nSource: {hit.get('doc_id')}: {hit.get('snippet', '')[:150]}"[
                    :2000
                ],
                "severity": match.group(1).upper(),
            }
    state = status.get("status") if status else None
    if "incident" not in objective.lower() or state not in _DEFAULT_SEVERITY:
        return None
    severity = _DEFAULT_SEVERITY[state]
    rule = re.compile(rf"(?i)\b{state}\b.*?\b(SEV[1-4])\b")
    for hit in snippets:
        found = next((m for line in hit.get("snippet", "").splitlines() if (m := rule.search(line))), None)
        if found:
            severity = found.group(1).upper()
            break
    return {
        "title": f"{service} is {state}",
        "description": (
            f"{objective[:1800]}\nStatus: {state}, p95 {status['latency_p95_ms']} ms, "
            f"error rate {status['error_rate']:.1%}."
        )[:2000],
        "severity": severity,
    }


def _answer(service: str | None, status: dict | None, results: dict[str, dict]) -> str:
    if not service:
        lines = ["No service found in the objective."]
    elif status:
        p95, rate = status["latency_p95_ms"], status["error_rate"]
        lines = [f"{service} is {status['status']}: p95 {p95} ms, error rate {rate:.1%}."]
    else:
        error = (results.get("get_service_status") or {}).get("error", {})
        lines = [f"Could not get the status of {service}: {error.get('message', 'no result')}."]
    incident = results.get("create_incident")
    if incident and incident.get("ok"):
        lines.append(f"Opened incident {incident['data']['incident_id']}.")
    elif incident:
        lines.append(f"No incident opened: {incident['error']['message'].rstrip('.')}.")
    return " ".join(lines)
