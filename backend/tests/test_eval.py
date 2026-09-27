import asyncio
import math
import os
import re
import subprocess
import sys

import pytest
from conftest import FakeEmbedder, FakeJudge
from pydantic import ValidationError
from qdrant_client import AsyncQdrantClient

from app.config import ROOT, Settings, cfg, settings
from app.eval import metrics
from app.eval.golden import MODES, GoldenItem, _summary, load_golden, run_golden
from app.eval.metrics import RagasJudge, doc_ranking, hit_at_k, recall_at_k, reciprocal_rank, safe_score
from app.kb.ingest import ingest, load_chunks
from app.kb.qdrant import KnowledgeBase

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
    metrics.get_judge.cache_clear()
    try:
        assert metrics.get_judge() is metrics.get_judge() is built[0]
        assert len(built) == 1  # built once, from settings, no network call
    finally:
        metrics.get_judge.cache_clear()


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
