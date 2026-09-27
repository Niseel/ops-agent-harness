import asyncio
import json
import logging
import math
import os
import re
import subprocess
import sys
from dataclasses import replace

import pytest
from conftest import FakeEmbedder, FakeJudge
from pydantic import ValidationError
from qdrant_client import AsyncQdrantClient

from app.config import ROOT, Settings, cfg, settings
from app.eval import metrics, online
from app.eval.golden import MODES, GoldenItem, _summary, load_golden, run_golden
from app.eval.metrics import RagasJudge, doc_ranking, hit_at_k, recall_at_k, reciprocal_rank, safe_score
from app.kb.ingest import ingest, load_chunks
from app.kb.qdrant import KnowledgeBase, Search
from app.llm import fake
from app.tools import registry

# Taken before the autouse no_real_judge fixture replaces metrics.get_judge.
_real_get_judge = metrics.get_judge

ITEMS = [
    GoldenItem(
        question="What does error 53300 mean?",
        reference="Too many connections.",
        relevant_doc_ids=["runbook-orders-db"],
    ),
    GoldenItem(
        question="What severity is a service that is down?", reference="SEV1.", relevant_doc_ids=["severity-policy"]
    ),
]


def test_hit_at_k_and_mrr():
    hits = [{"doc_id": d} for d in ["a", "a", "b", "c", "d"]]
    ranking = doc_ranking(hits)
    assert ranking == ["a", "b", "c", "d"]  # duplicates dropped, first stays
    assert hit_at_k(ranking, ["c"], 3) == 1.0 and hit_at_k(ranking, ["d"], 3) == 0.0
    assert reciprocal_rank(ranking, ["c", "b"], 10) == 0.5 and reciprocal_rank(ranking, ["z"], 10) == 0.0
    assert reciprocal_rank(ranking, ["d"], 3) == 0.0  # beyond k
    assert recall_at_k(ranking, ["a", "d"], 3) == 0.5 and recall_at_k(ranking, ["a", "a"], 3) == 1.0


async def test_safe_score_gives_null_with_reason(monkeypatch):
    async def value(v):
        return v

    async def boom():
        raise RuntimeError(f"judge said no, key {settings.llm_api_key}")

    monkeypatch.setattr(settings, "llm_api_key", "sk-judge-secret-999")
    assert await safe_score(value(0.25)) == (0.25, None)
    assert await safe_score(value(math.nan)) == (None, "no value (NaN)")
    not_a_number, why_not = await safe_score(value("0.5"))
    assert not_a_number is None and why_not.startswith("TypeError")  # a null row, not an aborted run
    value_, why = await safe_score(boom())
    assert value_ is None and why == "RuntimeError: judge said no, key ***"
    _, long_reason = await safe_score(value_error("x" * 500))
    assert len(long_reason) == 200


async def value_error(message):
    raise ValueError(message)


def test_golden_set_matches_kb():
    items = load_golden()
    assert 14 <= len(items) <= 20
    docs = {c.doc_id for c in load_chunks(settings.data_dir / "kb")}
    for item in items:
        assert set(item.relevant_doc_ids) <= docs, item.question
        assert "vendor-sms-note" not in item.relevant_doc_ids
    codes = ["PSP_GATEWAY_TIMEOUT", "ECONNRESET", "53300", "CircuitBreakingException", "SKU_SYNC_CONFLICT"]
    assert all(any(code in item.question for item in items) for code in codes)
    with pytest.raises(ValueError):
        GoldenItem.model_validate({"question": "abc?", "reference": "ref", "relevant_doc_ids": ["x"], "extra": 1})
    for code in codes:  # "one per code": each names exactly one question, not several
        assert sum(code in item.question for item in items) == 1, code


def test_load_golden_refuses_empty_relevant_doc_ids():
    with pytest.raises(ValueError):
        GoldenItem.model_validate({"question": "abc?", "reference": "ref", "relevant_doc_ids": []})


def test_golden_set_paraphrases_avoid_the_section_words():
    """At least 4 questions paraphrase a section without reusing any of its 4+ letter words (fixture rule)."""
    chunks = {(c.doc_id, c.section): c for c in load_chunks(settings.data_dir / "kb")}
    pairs = [
        (
            "The main Postgres node refuses all clients. How do we get writes working again?",
            "runbook-orders-db",
            "Database down",
        ),
        (
            "Users get kicked out because signatures on session credentials look wrong. What helps?",
            "runbook-auth-service",
            "Token errors",
        ),
        (
            "Thousands of outgoing mails are piling up unsent. What should we try?",
            "runbook-notifications-worker",
            "Queue backlog",
        ),
        ("Buyers wait ages before card charges finish. Where do we begin?", "runbook-payments-api", "High latency"),
    ]
    questions = {item.question for item in load_golden()}

    def words4(text):
        return {w for w in re.findall(r"[a-zA-Z]+", text.lower()) if len(w) >= 4}

    for question, doc_id, section in pairs:
        assert question in questions  # the golden set still has this question
        overlap = words4(question) & words4(chunks[(doc_id, section)].indexed)
        assert overlap == set(), (question, overlap)


async def test_golden_report_has_all_modes(kb, store):
    judge = FakeJudge(scores={"context_precision": 0.8, "context_recall": 0.6})
    ticks = []

    async def progress(done, total):
        ticks.append((done, total))

    report = await run_golden(kb, judge, store, progress=progress, golden=ITEMS)
    assert ticks == [(i, 6) for i in range(1, 7)]
    assert report["models"] == {"embed_model": "fake-embed", "judge_model": "fake-judge"}
    assert report["config"]["modes"] == list(MODES) and report["config"]["kb"]["top_n"] == cfg.kb.top_n
    assert list(report["summary"]) == list(MODES)
    for mode in MODES:
        summary = report["summary"][mode]
        assert summary["questions"] == 2 and summary["errors"] == 0 and summary["judge_error"] is None
        assert summary["context_precision"] == 0.8 and summary["context_recall"] == 0.6
        assert 0 <= summary["hit@3"] <= 1 and 0 <= summary["mrr@10"] <= 1 and 0 <= summary["recall@3"] <= 1
    first = report["rows"][0]
    assert (
        first["mode"] == "hybrid"
        and first["mode_used"] == "hybrid"
        and first["retrieved_doc_ids"][0] == "runbook-orders-db"
    )
    assert (first["hit@3"], first["rr@10"], first["recall@3"]) == (1.0, 1.0, 1.0)
    assert {r["mode_used"] for r in report["rows"]} == {"hybrid", "dense", "sparse"}
    assert [c[0] for c in judge.calls[:2]] == ["context_precision", "context_recall"]
    assert len(judge.calls[0][1][2]) == 3  # contexts: the text of the first 3 hits
    assert await store.latest_eval_report() == report


async def test_golden_report_without_judge_keeps_deterministic_metrics(kb, store):
    judge = FakeJudge(reachable=False)
    report = await run_golden(kb, judge, store, modes=("sparse",), golden=ITEMS)
    assert judge.calls == []
    summary = report["summary"]["sparse"]
    assert summary["judge_error"] == "judge unreachable" and summary["context_precision"] is None
    assert summary["hit@3"] == 1.0
    assert all(r["context_recall"] is None and r["judge_error"] == "judge unreachable" for r in report["rows"])


async def test_recall_at_k_with_two_relevant_docs(kb, store):
    two_relevant = GoldenItem(
        question="What does error 53300 mean?",
        reference="Too many connections.",
        relevant_doc_ids=["runbook-orders-db", "runbook-search-api"],  # only the first is actually retrieved
    )
    report = await run_golden(kb, FakeJudge(), store, modes=("sparse",), golden=[two_relevant])
    [row] = report["rows"]
    assert row["hit@3"] == 1.0  # one of the two relevant ids is enough for a hit
    assert row["recall@3"] == 0.5  # but only half of the relevant ids were found


async def test_run_golden_progress_is_optional(kb, store):
    report = await run_golden(kb, FakeJudge(), store, modes=("sparse",), golden=ITEMS)  # no progress given
    assert report["summary"]["sparse"]["questions"] == 2


def test_summary_excludes_error_rows_and_ignores_null_judge_values():
    rows = [
        {"error": None, "hit@3": 1.0, "rr@10": 1.0, "recall@3": 1.0, "context_precision": 0.8, "context_recall": 0.6},
        {"error": None, "hit@3": 0.0, "rr@10": 0.0, "recall@3": 0.0, "context_precision": None, "context_recall": None},
        {
            "error": "boom",
            "hit@3": None,
            "rr@10": None,
            "recall@3": None,
            "context_precision": None,
            "context_recall": None,
        },
    ]
    summary = _summary(rows, reachable=True)
    assert (summary["questions"], summary["errors"]) == (3, 1)
    assert summary["hit@3"] == 0.5  # mean over the two error-free rows only
    assert summary["context_precision"] == 0.8  # the None judge value is ignored, not averaged as 0


async def test_safe_score_does_not_swallow_cancelled_error():
    async def cancelled():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await safe_score(cancelled())


def test_get_judge_is_cached_without_network(monkeypatch):
    built = []
    monkeypatch.setattr(RagasJudge, "from_settings", classmethod(lambda cls: built.append(object()) or built[-1]))
    _real_get_judge.cache_clear()
    try:
        assert _real_get_judge() is _real_get_judge() is built[0]
        assert len(built) == 1  # built once, from settings, no network call
    finally:
        _real_get_judge.cache_clear()


def test_judge_json_mode_rejects_unknown_value(monkeypatch):
    monkeypatch.setenv("JUDGE_JSON_MODE", "bogus")
    with pytest.raises(ValidationError):
        Settings()


async def test_metric_failure_is_a_null_with_reason(kb, store):
    judge = FakeJudge(
        errors={"context_recall": TimeoutError("judge timed out")}, scores={"context_precision": math.nan}
    )
    report = await run_golden(kb, judge, store, modes=("hybrid",), golden=ITEMS[:1])
    [row] = report["rows"]
    assert (row["context_precision"], row["context_recall"]) == (None, None)
    assert row["judge_error"] == "no value (NaN)"  # the first reason wins
    assert report["summary"]["hybrid"]["context_precision"] is None


async def test_dense_mode_without_dense_index_is_an_error_row(store):
    kb = KnowledgeBase(AsyncQdrantClient(location=":memory:"), FakeEmbedder(fail=True))
    try:
        await ingest(kb, settings.data_dir / "kb")
        report = await run_golden(kb, FakeJudge(), store, golden=ITEMS)
    finally:
        await kb.client.close()
    dense = [r for r in report["rows"] if r["mode"] == "dense"]
    assert all(r["error"].startswith("LookupError: no dense index") and r["hit@3"] is None for r in dense)
    assert report["summary"]["dense"] == {
        "questions": 2,
        "errors": 2,
        "hit@3": None,
        "mrr@10": None,
        "recall@3": None,
        "context_precision": None,
        "context_recall": None,
        "judge_error": None,
    }
    assert report["summary"]["sparse"]["errors"] == 0
    hybrid = [r for r in report["rows"] if r["mode"] == "hybrid"]
    assert {r["mode_used"] for r in hybrid} == {"sparse_only"}


def test_ragas_judge_builds_metrics(monkeypatch):
    import instructor

    monkeypatch.delenv("RAGAS_DO_NOT_TRACK", raising=False)
    judge = RagasJudge(base_url="http://judge.test/v1", api_key="k", model="judge-model", json_mode="md_json")
    built = judge.metrics()  # no network: building only wires the clients
    assert sorted(built) == sorted(metrics.METRICS)
    assert os.environ["RAGAS_DO_NOT_TRACK"] == "true"
    for metric in built.values():
        assert metric.llm.client.mode == instructor.Mode.MD_JSON and metric.llm.model == "judge-model"
    assert built["answer_relevancy"].strictness == cfg.eval.relevancy_strictness
    assert judge.metrics() is built  # built once


def test_judge_settings_fall_back_to_llm(monkeypatch):
    for name in ("judge_base_url", "judge_api_key", "judge_model"):
        monkeypatch.setattr(settings, name, "")
    monkeypatch.setattr(settings, "llm_base_url", "http://llm.test/v1")
    monkeypatch.setattr(settings, "llm_model", "llm-model")
    judge = RagasJudge.from_settings()
    assert judge.model == "llm-model" and str(judge._client.base_url).rstrip("/") == "http://llm.test/v1"
    assert judge._client.max_retries == 0


async def test_unreachable_judge_probe_is_false():
    judge = RagasJudge(base_url="http://127.0.0.1:9/v1", api_key="k", model="m", json_mode="json")
    assert await judge.reachable() is False


def test_ragas_not_imported_at_startup():
    code = (
        "import sys, app.main, app.harness.runner, app.tools.registry, app.eval.golden; "
        "print(sorted(m for m in ('ragas', 'instructor') if m in sys.modules))"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT / "backend", capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


@pytest.mark.live
async def test_ragas_judge_live(live_judge):
    contexts = ["A service that is down is SEV1.", "A degraded service with customer impact is SEV2."]
    for value in (
        await live_judge.context_precision("What severity is a service that is down?", "SEV1.", contexts),
        await live_judge.context_recall("What severity is a service that is down?", "SEV1.", contexts),
        await live_judge.context_relevance("What severity is a service that is down?", contexts),
        await live_judge.faithfulness("What severity is a down service?", "It is SEV1.", contexts),
        await live_judge.answer_relevancy("What severity is a down service?", "It is SEV1."),
    ):
        assert 0 <= value <= 1


# --- online evaluation (T4) -----------------------------------------------------------------


async def online_run(runner, monkeypatch, judge, objective="Why is payments-api slow?", **options):
    monkeypatch.setattr(metrics, "get_judge", lambda: judge)
    run = await runner.create_run(objective, options={"evaluate": True, **options})
    status = await runner.run_segment(run["id"])
    return run["id"], status


async def test_online_scores_stored_after_run(runner, search, monkeypatch):
    judge = FakeJudge()
    run_id, status = await online_run(runner, monkeypatch, judge)
    assert status == "completed"
    state = await runner.get_state(run_id)
    search_id = next(
        c["id"]
        for m in state["messages"]
        for c in m.get("tool_calls") or ()
        if c["function"]["name"] == "search_knowledge_base"
    )
    evals = await runner.store.list_evals(run_id)
    assert [(e["target"], e["metric"], e["value"], e["judge_model"], e["error"]) for e in evals] == [
        (f"search:{search_id}", "context_relevance", 0.9, "fake-judge", None),
        ("answer", "faithfulness", 0.9, "fake-judge", None),
        ("answer", "answer_relevancy", 0.9, "fake-judge", None),
    ]
    metric, (query, snippets) = judge.calls[0]
    assert metric == "context_relevance" and query == "Why is payments-api slow?" and len(snippets) == 3
    metric, (question, answer, contexts) = judge.calls[1]
    assert (metric, question, answer) == ("faithfulness", "Why is payments-api slow?", state["final"])
    assert contexts[0].startswith("payments-api runbook / High latency: ") and '"status": "degraded"' in contexts[-1]
    assert judge.calls[2] == ("answer_relevancy", ("Why is payments-api slow?", state["final"]))
    events = await runner.store.list_events(run_id)
    kinds = [e["kind"] for e in events]
    assert kinds[-4:] == ["done", "eval", "eval", "eval"]  # scores come after done
    first = events[-3]
    assert (first["node"], first["tool"], first["status"], first["attention"]) == (
        "eval",
        "search_knowledge_base",
        "ok",
        None,
    )
    assert first["data"] == {
        "target": f"search:{search_id}",
        "metric": "context_relevance",
        "value": 0.9,
        "judge_model": "fake-judge",
        "error": None,
        "threshold": cfg.eval.thresholds.context_relevance,
    }
    assert events[-1]["tool"] is None and events[-1]["data"]["threshold"] is None  # no threshold for answer relevancy


async def test_low_score_emits_warn(runner, search, monkeypatch):
    judge = FakeJudge(scores={"faithfulness": 0.5, "context_relevance": 0.4, "answer_relevancy": 0.1})
    run_id, _ = await online_run(runner, monkeypatch, judge)
    evals = [e for e in await runner.store.list_events(run_id) if e["kind"] == "eval"]
    assert [(e["data"]["metric"], e["attention"]) for e in evals] == [
        ("context_relevance", "warn"),
        ("faithfulness", "warn"),
        ("answer_relevancy", None),
    ]
    assert evals[1]["msg"] == "faithfulness 0.50 (below 0.7)"
    at_threshold = FakeJudge(scores={"faithfulness": cfg.eval.thresholds.faithfulness})
    run_id, _ = await online_run(runner, monkeypatch, at_threshold)
    faithfulness = next(
        e
        for e in await runner.store.list_events(run_id)
        if e["kind"] == "eval" and e["data"]["metric"] == "faithfulness"
    )
    assert faithfulness["attention"] is None  # strictly below warns


async def test_judge_down_gives_null_and_run_unchanged(runner, search, monkeypatch):
    judge = FakeJudge(reachable=False)
    run_id, status = await online_run(runner, monkeypatch, judge)
    assert status == "completed" and judge.calls == []
    evals = await runner.store.list_evals(run_id)
    assert len(evals) == 3 and all(e["value"] is None and e["error"] == "judge unreachable" for e in evals)
    events = await runner.store.list_events(run_id)
    scored = [e for e in events if e["kind"] == "eval"]
    assert all(e["attention"] == "info" and e["status"] == "error" for e in scored)
    assert scored[0]["msg"] == "context_relevance null: judge unreachable"
    assert [e["status"] for e in events if e["kind"] == "done"] == ["completed"]
    assert (await runner.store.get_run(run_id))["status"] == "completed"


async def test_evaluate_default_follows_online_default_and_judge(runner, monkeypatch):
    async def stored(**options):
        run = await runner.create_run("Check payments-api", options=options or None)
        return (await runner.store.get_run(run["id"]))["options"]["evaluate"]

    judge = FakeJudge()
    probes = []
    real_reachable = judge.reachable

    async def counted():
        probes.append(1)
        return await real_reachable()

    judge.reachable = counted
    monkeypatch.setattr(metrics, "get_judge", lambda: judge)
    monkeypatch.setattr(cfg.eval, "online_default", True)
    assert await stored() is True and len(probes) == 1
    judge.is_reachable = False
    assert await stored() is False and len(probes) == 2
    assert await stored(evaluate=True) is True and await stored(evaluate=False) is False  # given values kept
    assert len(probes) == 2  # no probe for a given value
    monkeypatch.setattr(cfg.eval, "online_default", False)
    judge.is_reachable = True
    assert await stored() is False and len(probes) == 2  # default off: the judge is not asked


async def test_metric_error_or_nan_gives_null_with_reason(runner, search, monkeypatch):
    judge = FakeJudge(errors={"faithfulness": RuntimeError("judge said no")}, scores={"answer_relevancy": math.nan})
    run_id, _ = await online_run(runner, monkeypatch, judge)
    evals = {e["metric"]: e for e in await runner.store.list_evals(run_id)}
    assert evals["context_relevance"]["value"] == 0.9
    assert (evals["faithfulness"]["value"], evals["faithfulness"]["error"]) == (None, "RuntimeError: judge said no")
    assert (evals["answer_relevancy"]["value"], evals["answer_relevancy"]["error"]) == (None, "no value (NaN)")


async def test_run_without_final_answer_gets_no_answer_rows(runner, search, monkeypatch):
    run_id, status = await online_run(runner, monkeypatch, FakeJudge(), limits={"max_steps": 1})
    assert status == "limit_exceeded"
    evals = await runner.store.list_evals(run_id)
    assert [(e["target"].split(":")[0], e["metric"]) for e in evals] == [("search", "context_relevance")]


async def test_evaluation_failure_never_changes_run(runner, search, monkeypatch, caplog):
    async def broken(*args, **kwargs):
        raise RuntimeError("evaluation bug")

    monkeypatch.setattr(online, "evaluate_run", broken)
    caplog.set_level(logging.ERROR, logger="app.eval")
    run_id, status = await online_run(runner, monkeypatch, FakeJudge())
    assert status == "completed"
    row = await runner.store.get_run(run_id)
    done = [e for e in await runner.store.list_events(run_id) if e["kind"] == "done"]
    assert (row["status"], row["error"], row["steps"], row["tool_calls"]) == ("completed", None, 3, 2)
    assert row["final"] and row["finished_at"] and len(done) == 1
    assert done[0]["data"] == {"status": "completed", "error": None, "steps": 3, "tool_calls": 2}
    assert [e["kind"] for e in await runner.store.list_events(run_id)][-1] == "done"
    failures = [r for r in caplog.records if r.name == "app.eval"]
    assert len(failures) == 1 and failures[0].getMessage() == "online evaluation failed" and failures[0].exc_info


async def test_evaluation_cancelled_error_propagates(runner, search, monkeypatch):
    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(online, "evaluate_run", cancelled)
    run = await runner.create_run("Why is payments-api slow?", options={"evaluate": True})
    with pytest.raises(asyncio.CancelledError):
        await runner.run_segment(run["id"])


async def test_evaluate_false_stores_no_rows_or_events(runner, search, monkeypatch):
    judge = FakeJudge()
    monkeypatch.setattr(metrics, "get_judge", lambda: judge)
    run = await runner.create_run("Why is payments-api slow?", options={"evaluate": False})
    status = await runner.run_segment(run["id"])
    assert status == "completed"
    assert await runner.store.list_evals(run["id"]) == []
    assert judge.calls == []
    kinds = {e["kind"] for e in await runner.store.list_events(run["id"])}
    assert "eval" not in kinds


async def test_paused_run_is_not_evaluated(runner, search, monkeypatch):
    judge = FakeJudge()
    monkeypatch.setattr(metrics, "get_judge", lambda: judge)
    run = await runner.create_run(
        "SMS alerts from notifications-worker are delayed. Check the SMS vendor note.", options={"evaluate": True}
    )
    status = await runner.run_segment(run["id"])
    assert status == "awaiting_approval"
    assert await runner.store.list_evals(run["id"]) == []
    assert judge.calls == []
    kinds = {e["kind"] for e in await runner.store.list_events(run["id"])}
    assert "eval" not in kinds


async def test_run_without_search_gets_only_answer_rows(runner, monkeypatch):
    judge = FakeJudge()
    monkeypatch.setattr(metrics, "get_judge", lambda: judge)
    llm = fake.ScriptedLLM([fake.final("all good")])
    run = await runner.create_run("Say hello", options={"evaluate": True})
    status = await runner.run_segment(run["id"], llm_client=llm)
    assert status == "completed"
    evals = {e["metric"]: e for e in await runner.store.list_evals(run["id"])}
    assert set(evals) == {"faithfulness", "answer_relevancy"}
    assert (evals["faithfulness"]["value"], evals["faithfulness"]["error"]) == (None, "no contexts")
    assert evals["answer_relevancy"]["value"] == 0.9
    # faithfulness is never called at all: there are no contexts to score against
    assert judge.calls == [("answer_relevancy", ("Say hello", "all good"))]


async def test_failed_search_is_not_scored(runner, monkeypatch):
    judge = FakeJudge()
    monkeypatch.setattr(metrics, "get_judge", lambda: judge)
    llm = fake.ScriptedLLM(
        [fake.calls(("search_knowledge_base", {"query": "payments-api 5xx errors"})), fake.final("no answer")]
    )
    run = await runner.create_run(
        "Check payments-api",
        options={"evaluate": True, "faults": {"search_knowledge_base": {"mode": "error", "times": 3}}},
    )
    status = await runner.run_segment(run["id"], llm_client=llm)
    assert status == "completed"
    state = await runner.get_state(run["id"])
    [tool_message] = [m for m in state["messages"] if m["role"] == "tool"]
    assert json.loads(tool_message["content"])["ok"] is False  # the search failed, every attempt
    evals = {e["metric"]: e for e in await runner.store.list_evals(run["id"])}
    assert set(evals) == {"faithfulness", "answer_relevancy"}  # no search:<id> row
    assert evals["faithfulness"]["error"] == "no contexts"  # a failed search adds no context either


async def test_zero_result_search_is_not_scored(runner, search, monkeypatch):
    async def no_hits(*args, **kwargs):
        return Search(hits=[], mode="hybrid", embed_error=None, dense=[], bm25=[])

    monkeypatch.setattr(search, "search", no_hits)
    judge = FakeJudge()
    monkeypatch.setattr(metrics, "get_judge", lambda: judge)
    llm = fake.ScriptedLLM(
        [fake.calls(("search_knowledge_base", {"query": "no matches at all"})), fake.final("nothing found")]
    )
    run = await runner.create_run("Check payments-api", options={"evaluate": True})
    status = await runner.run_segment(run["id"], llm_client=llm)
    assert status == "completed"
    evals = {e["metric"]: e for e in await runner.store.list_evals(run["id"])}
    assert set(evals) == {"faithfulness", "answer_relevancy"}
    assert evals["faithfulness"]["error"] == "no contexts"


async def test_two_searches_give_rows_in_message_order(runner, search, monkeypatch):
    judge = FakeJudge()
    monkeypatch.setattr(metrics, "get_judge", lambda: judge)
    llm = fake.ScriptedLLM(
        [
            fake.calls(("search_knowledge_base", {"query": "payments-api 5xx errors"}, "c0")),
            fake.calls(("search_knowledge_base", {"query": "auth-service login failures"}, "c1")),
            fake.final("done"),
        ]
    )
    run = await runner.create_run("Check things", options={"evaluate": True})
    status = await runner.run_segment(run["id"], llm_client=llm)
    assert status == "completed"
    evals = await runner.store.list_evals(run["id"])
    assert [e["target"] for e in evals] == ["search:c0", "search:c1", "answer", "answer"]


async def test_failed_run_after_search_is_evaluated(runner, search, monkeypatch):
    judge = FakeJudge()
    monkeypatch.setattr(metrics, "get_judge", lambda: judge)
    llm = fake.ScriptedLLM(
        [
            fake.calls(("search_knowledge_base", {"query": "payments-api 5xx errors"})),
            fake.raw(finish_reason="length"),
            fake.raw(finish_reason="length"),
        ]
    )
    run = await runner.create_run("Check payments-api", options={"evaluate": True, "limits": {"max_repairs": 1}})
    status = await runner.run_segment(run["id"], llm_client=llm)
    assert status == "failed"
    evals = await runner.store.list_evals(run["id"])
    assert [e["metric"] for e in evals] == ["context_relevance"]  # no final answer: no answer rows
    assert evals[0]["value"] == 0.9


async def test_timed_out_run_is_still_evaluated(runner, search, monkeypatch):
    async def slow(args, ctx):
        await asyncio.sleep(2)
        return {}

    tool = registry.TOOLS["get_service_status"]
    monkeypatch.setitem(registry.TOOLS, tool.name, replace(tool, run=slow))
    monkeypatch.setattr(cfg.tools["get_service_status"], "timeout_s", 5)  # the segment limit fires first
    judge = FakeJudge()
    monkeypatch.setattr(metrics, "get_judge", lambda: judge)
    llm = fake.ScriptedLLM(
        [
            fake.calls(("search_knowledge_base", {"query": "payments-api 5xx errors"})),
            fake.calls(("get_service_status", {"service_name": "payments-api"})),
        ]
    )
    run = await runner.create_run("Check payments-api", options={"evaluate": True, "limits": {"max_run_seconds": 1}})
    status = await runner.run_segment(run["id"], llm_client=llm)
    assert status == "timed_out"
    evals = await runner.store.list_evals(run["id"])
    assert [e["metric"] for e in evals] == ["context_relevance"]  # the search still scored, outside the deadline
    events = await runner.store.list_events(run["id"])
    assert [e["kind"] for e in events][-2:] == ["done", "eval"]


async def test_online_eval_error_reasons_are_masked(runner, search, monkeypatch):
    secret = settings.llm_api_key  # "lm-studio" by default, already in Tracer.secrets
    judge = FakeJudge(errors={"faithfulness": RuntimeError(f"upstream rejected key {secret}")})
    run_id, status = await online_run(runner, monkeypatch, judge)
    assert status == "completed"
    row = next(e for e in await runner.store.list_evals(run_id) if e["metric"] == "faithfulness")
    assert secret not in (row["error"] or "") and "***" in row["error"]
    event = next(
        e
        for e in await runner.store.list_events(run_id)
        if e["kind"] == "eval" and e["data"]["metric"] == "faithfulness"
    )
    assert secret not in event["msg"]
    assert secret not in json.dumps(event["data"])


async def test_nothing_to_score_does_not_probe_the_judge(store, tracer):
    judge = FakeJudge()
    probed = []
    judge.reachable = lambda: probed.append(1)  # would fail if awaited
    assert (
        await online.evaluate_run("r1", {"messages": [], "final": None}, judge=judge, store=store, tracer=tracer) == []
    )
    assert probed == [] and await store.list_evals("r1") == []
