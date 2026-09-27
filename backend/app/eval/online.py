"""Online evaluation of one finished run (spec: Evaluation, Online; ADR 0008).

The runner calls `evaluate_run` right after the run's `done` event. It scores
every search that returned results (context relevance) and, when the run has
a final answer, the answer (faithfulness against all tool outputs, answer
relevancy). Scores are stored in `evals` and emitted as `eval` events; they
never change the run.
"""

import json

from app.config import cfg
from app.eval.metrics import safe_score

SEARCH = "search_knowledge_base"


async def evaluate_run(run_id: str, state: dict, *, judge, store, tracer) -> list[dict]:
    """Store and emit one row per target and metric; return the rows in order."""
    searches, contexts = _tool_results(state.get("messages", []))
    plan = [(f"search:{call_id}", "context_relevance", (query, snippets)) for call_id, query, snippets in searches]
    if state.get("final"):
        objective, final = state["objective"], state["final"]
        plan.append(("answer", "faithfulness", (objective, final, contexts)))
        plan.append(("answer", "answer_relevancy", (objective, final)))

    if not plan:
        return []  # nothing to score: do not probe the judge
    reachable = await judge.reachable()  # probed once: a judge that is down is not asked per metric
    rows = []
    for target, metric, args in plan:  # one call at a time: a local judge serves one request at a time
        if not reachable:
            value, error = None, "judge unreachable"
        elif metric == "faithfulness" and not args[2]:
            value, error = None, "no contexts"
        else:
            value, error = await safe_score(getattr(judge, metric)(*args))
        error = tracer.mask(error)
        row = {"target": target, "metric": metric, "value": value, "judge_model": judge.model, "error": error}
        await store.insert_eval(run_id, **row)
        await _event(tracer, run_id, row)
        rows.append(row)
    return rows


def _tool_results(messages: list[dict]) -> tuple[list[tuple[str, str, list[str]]], list[str]]:
    """Searches with results as (tool_call_id, query, snippets), and every ok tool output as context text."""
    calls = {
        call["id"]: call["function"]
        for message in messages
        if message.get("role") == "assistant"
        for call in message.get("tool_calls") or ()
    }
    searches, contexts = [], []
    for message in messages:
        if message.get("role") != "tool" or message.get("tool_call_id") not in calls:
            continue
        envelope = json.loads(message["content"])
        if not envelope.get("ok"):
            continue
        function, data = calls[message["tool_call_id"]], envelope["data"]
        if function["name"] == SEARCH and isinstance(data, dict):
            hits = data.get("results") or []
            contexts += [f"{hit['title']} / {hit['section']}: {hit['snippet']}" for hit in hits]
            if hits:
                query = json.loads(function["arguments"]).get("query", "")
                searches.append((message["tool_call_id"], query, [hit["snippet"] for hit in hits]))
        else:
            contexts.append(json.dumps(data))
    return searches, contexts


async def _event(tracer, run_id: str, row: dict) -> None:
    metric, value, error = row["metric"], row["value"], row["error"]
    threshold = getattr(cfg.eval.thresholds, metric, None)
    if value is None:
        attention, msg = "info", f"{metric} null: {error}"
    elif threshold is not None and value < threshold:
        attention, msg = "warn", f"{metric} {value:.2f} (below {threshold})"
    else:
        attention, msg = None, f"{metric} {value:.2f}"
    await tracer.emit(
        run_id,
        "eval",
        node="eval",
        tool=SEARCH if row["target"].startswith("search:") else None,
        status="ok" if value is not None else "error",
        attention=attention,
        msg=msg,
        data={**row, "threshold": threshold},
    )
