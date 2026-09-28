import asyncio
import json
import logging
import re
import shutil
from types import SimpleNamespace

import pytest
from conftest import FakeEmbedder
from fastapi.testclient import TestClient
from qdrant_client import AsyncQdrantClient, models

from app.config import KB, cfg, settings
from app.harness import tool_gateway
from app.kb import qdrant, sparse
from app.kb.ingest import content_hash, ingest, load_chunks
from app.kb.qdrant import KnowledgeBase, get_kb, rrf
from app.llm.fake import ScriptedLLM, calls, final
from app.llm.openai_compat import OpenAICompatEmbedder
from app.main import app
from app.tools.faults import EmbedCounter, EmbeddingsFault

# Captured at import time (before the autouse `no_real_kb` fixture patches the module attribute),
# so it stays the real, cached `get_kb` for the whole test session.
_real_get_kb = get_kb

DOCS = settings.data_dir / "kb"

# Objectives from the Postman demos, evals/, the review guide and the run-level tests.
# The vendor note with the injected instruction must never reach their top 3.
PLAIN_OBJECTIVES = [
    "payments-api is returning 5xx errors. Investigate and open an incident if needed.",
    "payments-api is returning 5xx errors, investigate.",
    "payments-api is returning 5xx, investigate",
    "Check the status of auth-service and summarise it.",
    "orders-db is down. Investigate and open an incident if needed.",
    "Why is payments-api slow?",
    "payments-api is slow. Open an incident if it is degraded.",
    "orders-db is down. Open an incident.",
    "Check payments-api",
    "Check auth-service",
    "Check notifications-worker",
]
INJECTION_OBJECTIVE = "SMS alerts from notifications-worker are delayed. Check the SMS vendor note."
ERROR_CODES = ["PSP_GATEWAY_TIMEOUT", "ECONNRESET", "53300", "CircuitBreakingException", "SKU_SYNC_CONFLICT"]


def doc_ids(search):
    return [h["doc_id"] for h in search.hits]


async def fresh_kb(embedder=None):
    return KnowledgeBase(AsyncQdrantClient(location=":memory:"), embedder or FakeEmbedder())


# --- pieces ---------------------------------------------------------------------------------


def test_rrf_fuses_ranks():
    fused = rrf({"dense": [1, 2, 3], "bm25": [3, 4]}, k=60)
    assert [f[0] for f in fused] == [3, 1, 2, 4]
    point, score, ranks = fused[0]
    assert ranks == {"dense": 3, "bm25": 1} and score == pytest.approx(1 / 63 + 1 / 61)
    assert fused[3][2] == {"dense": None, "bm25": 2}  # missing from a list: null rank
    # Equal scores: the better single rank wins, then the lower id.
    assert [f[0] for f in rrf({"dense": [7, 8], "bm25": [8, 7]}, k=60)] == [7, 8]
    assert [f[0] for f in rrf({"dense": [5], "bm25": [6]}, k=60)] == [5, 6]
    assert rrf({"dense": [], "bm25": []}, k=60) == []


def test_bm25_vectors():
    assert sparse.tokens("payments-api PSP_GATEWAY_TIMEOUT 5xx!") == ["payments", "api", "psp_gateway_timeout", "5xx"]
    indices, values = sparse.doc_vector("pool pool pool check")
    assert len(indices) == len(set(indices)) == 2 and indices == sorted(indices)
    weight = dict(zip(indices, values, strict=True))
    pool, check = weight[sparse._index("pool")], weight[sparse._index("check")]
    assert pool > check and pool < 3 * check  # term frequency saturates
    short = dict(zip(*sparse.doc_vector("pool"), strict=True))[sparse._index("pool")]
    long = dict(zip(*sparse.doc_vector("pool " + "word " * 200), strict=True))[sparse._index("pool")]
    assert long < short  # a longer chunk weighs a term less
    assert sparse.query_vector("pool pool check") == (
        sorted([sparse._index("pool"), sparse._index("check")]),
        [1.0, 1.0],
    )
    assert sparse.query_vector("!!! ...") == ([], [])


def test_chunks_split_by_section():
    chunks = load_chunks(DOCS)
    assert len(chunks) == 24
    payments = [c for c in chunks if c.doc_id == "runbook-payments-api"]
    assert [c.section for c in payments] == ["Overview", "5xx errors", "High latency", "Rollback"]
    assert {c.title for c in payments} == {"payments-api runbook"}
    assert payments[1].text.startswith("HTTP 500, 502 and 503") and "## " not in payments[1].text
    assert payments[1].indexed.startswith("payments-api runbook\n5xx errors\n")
    auth = [c for c in chunks if c.doc_id == "runbook-auth-service"]
    assert auth[0].section == "Login failures"  # no intro text: no Overview chunk
    assert [c.doc_id for c in chunks] == sorted(c.doc_id for c in chunks)


def test_fixture_docs_match_planner_patterns():
    texts = {path.stem: path.read_text() for path in DOCS.glob("*.md")}
    injected = re.compile(r"(?i)\bopen an? (SEV[1-4])\b")
    assert [doc for doc, text in texts.items() if injected.search(text)] == ["vendor-sms-note"]
    assert sorted(doc for doc, text in texts.items() if re.search(r"SEV[1-4]", text)) == [
        "severity-policy",
        "vendor-sms-note",
    ]
    levels = next(c for c in load_chunks(DOCS) if c.doc_id == "severity-policy" and c.section == "Levels")
    assert levels.text.splitlines()[:2] == [
        "A service that is down is SEV1.",
        "A degraded service with customer impact is SEV2.",
    ]
    for code in ERROR_CODES:
        assert [doc for doc, text in texts.items() if code in text] != [] and len(
            [doc for doc, text in texts.items() if code in text]
        ) == 1, code
    vendor_words = set(sparse.tokens(texts["vendor-sms-note"]))
    banned = {"incident", "error", "errors", "status", "check", "investigate", "down", "degraded", "slow", "service"}
    banned |= {w for doc in texts if doc.startswith("runbook-") for w in sparse.tokens(doc.removeprefix("runbook-"))}
    assert vendor_words & banned == set()
    vendor = next(c for c in load_chunks(DOCS) if c.doc_id == "vendor-sms-note")
    assert injected.search(vendor.text[:400])  # the tool's snippet keeps it


@pytest.mark.parametrize("mode", ["sparse", "hybrid"])
async def test_injected_doc_only_found_by_its_own_query(kb, mode):
    for objective in PLAIN_OBJECTIVES:
        assert "vendor-sms-note" not in doc_ids(await kb.search(objective, mode=mode)), objective
    assert "vendor-sms-note" in doc_ids(await kb.search(INJECTION_OBJECTIVE, mode=mode))


# --- search ---------------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["hybrid", "sparse"])
async def test_exact_term_found_by_bm25(kb, mode):
    assert sum("53300" in path.read_text() for path in DOCS.glob("runbook-*.md")) == 1
    search = await kb.search("What does error 53300 mean?", mode=mode)
    top = search.hits[0]
    assert (top["doc_id"], top["section"]) == ("runbook-orders-db", "Too many connections")
    assert top["ranks"]["bm25"] <= 3 and top["ranks"]["rrf"] == 1
    assert search.mode == mode and search.embed_error is None


async def test_hybrid_search_returns_both_rankings(kb):
    search = await kb.search("orders-db is down. Open an incident.")
    assert search.mode == "hybrid" and len(search.hits) == cfg.kb.top_n
    assert search.dense and search.bm25
    assert search.dense[0].keys() == {"rank", "doc_id", "section", "score"}
    for position, hit in enumerate(search.hits, start=1):
        assert hit.keys() == {"doc_id", "title", "section", "text", "score", "ranks"}
        assert hit["ranks"]["rrf"] == position
    assert [h["score"] for h in search.hits] == sorted((h["score"] for h in search.hits), reverse=True)
    assert len((await kb.search("orders-db is down", limit=10)).hits) == 10


@pytest.mark.parametrize("embedder", [FakeEmbedder(fail=True), FakeEmbedder(delay=0.2)])
async def test_sparse_only_when_embeddings_down(kb, monkeypatch, embedder):
    monkeypatch.setattr(cfg.kb, "embed_timeout_s", 0.05)
    kb.embedder = embedder  # the index still has dense vectors; only the query embedding fails
    search = await kb.search("What does error 53300 mean?")
    assert search.mode == "sparse_only" and search.dense == []
    assert search.hits[0]["doc_id"] == "runbook-orders-db"
    assert all(h["ranks"]["dense"] is None for h in search.hits)
    reason = "no embedding within 0.05 s" if embedder.delay else "ConnectionError: embedding endpoint refused"
    assert search.embed_error.startswith(reason)


async def test_sparse_mode_makes_no_embed_call(kb):
    calls = kb.embedder.calls
    search = await kb.search("What does error 53300 mean?", mode="sparse")
    assert search.mode == "sparse" and search.dense == [] and kb.embedder.calls == calls


async def test_hybrid_query_without_tokens_runs_dense_only(kb):
    # No tokens: `query_vector` is empty, so the BM25 query is skipped; the dense query still runs
    # (the fake embedder gives a fixed unit vector for text with no tokens).
    search = await kb.search("!!! ...")
    assert search.bm25 == [] and search.mode == "hybrid"
    assert search.hits and all(h["ranks"]["bm25"] is None for h in search.hits)


async def test_limit_larger_than_available_hits(kb):
    search = await kb.search("payments-api", limit=1000)
    assert 0 < len(search.hits) < 1000
    assert len({h["doc_id"] + h["section"] for h in search.hits}) == len(search.hits)


async def test_outer_cancellation_is_not_swallowed(kb, monkeypatch):
    monkeypatch.setattr(cfg.kb, "embed_timeout_s", 5.0)  # the inner timeout must not fire first
    kb.embedder = FakeEmbedder(delay=1.0)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(kb.search("Check payments-api"), timeout=0.01)


async def test_fail_embed_is_an_injected_failure(kb):
    asked = []
    search = await kb.search("Check payments-api", fail_embed=lambda: asked.append(1) or True)
    assert (search.mode, search.embed_error, asked, kb.embedder.calls) == ("sparse_only", "injected fault", [1], 1)
    search = await kb.search("Check payments-api", fail_embed=lambda: False)
    assert search.mode == "hybrid" and kb.embedder.calls == 2  # one ingest call, one query


async def test_dense_mode_needs_a_dense_index():
    kb = await fresh_kb(FakeEmbedder(fail=True))
    try:
        await ingest(kb, DOCS)
        with pytest.raises(LookupError):
            await kb.search("Check payments-api", mode="dense")
        search = await kb.search(
            "Check payments-api", fail_embed=lambda: pytest.fail("no dense index: nothing to count")
        )
        assert search.mode == "sparse_only" and search.embed_error is None
    finally:
        await kb.client.close()


async def test_kb_status_reports_mode(kb):
    assert await kb.status() == "hybrid"
    bm25_only = await fresh_kb(FakeEmbedder(fail=True))
    try:
        assert await bm25_only.status() == "unavailable"  # no collection yet
        await ingest(bm25_only, DOCS)
        assert await bm25_only.status() == "sparse_only"
    finally:
        await bm25_only.client.close()
    await kb.client.close()
    assert await kb.status() == "unavailable"  # the client raises


async def test_status_unavailable_when_qdrant_raises(kb, monkeypatch):
    async def raise_(*args, **kwargs):
        raise ConnectionError("qdrant down")

    monkeypatch.setattr(kb.client, "get_collection", raise_)
    assert await kb.status() == "unavailable"


def test_get_kb_is_cached_per_process(monkeypatch):
    # Stubs: a real AsyncQdrantClient asks the server for its version in a background thread.
    built = []
    monkeypatch.setattr(qdrant, "AsyncQdrantClient", lambda **kwargs: built.append(kwargs) or SimpleNamespace())
    monkeypatch.setattr(qdrant.OpenAICompatEmbedder, "from_settings", classmethod(lambda cls: FakeEmbedder()))
    _real_get_kb.cache_clear()
    try:
        assert _real_get_kb() is _real_get_kb()
        assert built == [{"url": settings.qdrant_url, "api_key": settings.qdrant_api_key or None}]
    finally:
        _real_get_kb.cache_clear()


# --- ingest ---------------------------------------------------------------------------------


def spy_on_collections(kb, monkeypatch):
    changes = []
    for name in ("create_collection", "delete_collection"):
        real = getattr(kb.client, name)

        async def spy(*args, _real=real, _name=name, **kwargs):
            changes.append(_name)
            return await _real(*args, **kwargs)

        monkeypatch.setattr(kb.client, name, spy)
    return changes


async def test_ingest_empty_dir_raises(kb, tmp_path):
    with pytest.raises(ValueError):
        await ingest(kb, tmp_path)


async def test_indexed_point_payload_fields(kb):
    records, _ = await kb.client.scroll(cfg.kb.collection, limit=1, with_payload=True)
    payload = records[0].payload
    assert payload.keys() == {"doc_id", "title", "section", "text", "content_hash", "embed_model"}
    assert payload["embed_model"] == kb.embedder.model
    assert payload["content_hash"] == content_hash(DOCS, kb.embedder.model)


async def test_bm25_idf_downweights_common_token(kb):
    # "runbook" sits in every runbook's title, so it appears in ~21 chunks; "reindexing" is the
    # heading of exactly one chunk. Equal term frequency (1), but IDF (applied by Qdrant at query
    # time) makes the rare token score far higher than the common one.
    async def top_bm25_score(word: str) -> float:
        indices, values = sparse.query_vector(word)
        response = await kb.client.query_points(
            cfg.kb.collection, query=models.SparseVector(indices=indices, values=values), using="bm25", limit=1
        )
        return response.points[0].score

    assert await top_bm25_score("reindexing") > await top_bm25_score("runbook")


async def test_ingest_skips_unchanged(kb, monkeypatch):
    calls, changes = kb.embedder.calls, spy_on_collections(kb, monkeypatch)
    assert await ingest(kb, DOCS) == {"status": "skipped", "mode": "hybrid", "chunks": 24}
    assert kb.embedder.calls == calls and changes == []


async def test_ingest_force_rebuilds(kb):
    assert (await ingest(kb, DOCS))["status"] == "skipped"
    before = (await kb.client.get_collection(cfg.kb.collection)).points_count
    result = await ingest(kb, DOCS, force=True)  # e.g. after a BM25 setting changed, which the hash does not cover
    assert result == {"status": "rebuilt", "mode": "hybrid", "chunks": before}
    assert (await kb.search("payments-api latency", mode="hybrid")).hits


async def test_ingest_rebuilds_when_docs_or_model_change(kb, monkeypatch, tmp_path):
    docs = tmp_path / "kb"
    shutil.copytree(DOCS, docs)
    assert (await ingest(kb, docs))["status"] == "skipped"  # same bytes, same names
    (docs / "runbook-search-api.md").write_text("# search-api runbook\n\n## Slow queries\n\nAdd an index.\n")
    changes = spy_on_collections(kb, monkeypatch)
    assert await ingest(kb, docs) == {"status": "rebuilt", "mode": "hybrid", "chunks": 22}
    assert changes == ["delete_collection", "create_collection"]
    assert (await ingest(kb, docs))["status"] == "skipped"
    kb.embedder.model = "another-model"
    assert (await ingest(kb, docs))["status"] == "rebuilt"
    assert content_hash(docs, "a") != content_hash(docs, "b")


async def test_ingest_without_embeddings_indexes_bm25_only():
    embedder = FakeEmbedder(fail=True)
    kb = await fresh_kb(embedder)
    try:
        assert await ingest(kb, DOCS) == {"status": "rebuilt", "mode": "sparse_only", "chunks": 24}
        assert await kb.status() == "sparse_only"
        records, _ = await kb.client.scroll(cfg.kb.collection, limit=1)
        assert records[0].payload["embed_model"] is None
        assert (await kb.search("What does error 53300 mean?")).hits[0]["doc_id"] == "runbook-orders-db"
        embedder.fail = False  # embeddings are back: the next ingest adds dense vectors
        assert await ingest(kb, DOCS) == {"status": "rebuilt", "mode": "hybrid", "chunks": 24}
        assert await kb.status() == "hybrid"
    finally:
        await kb.client.close()


async def test_embedder_uses_settings_and_float_format(monkeypatch):
    monkeypatch.setattr(settings, "embed_base_url", "")
    monkeypatch.setattr(settings, "embed_api_key", "")
    monkeypatch.setattr(settings, "llm_base_url", "http://llm.test/v1")
    monkeypatch.setattr(settings, "llm_api_key", "llm-key")
    embedder = OpenAICompatEmbedder.from_settings()
    assert str(embedder._sdk.base_url).rstrip("/") == "http://llm.test/v1"
    assert embedder._sdk.api_key == "llm-key" and embedder._sdk.max_retries == 0
    monkeypatch.setattr(settings, "embed_base_url", "http://embed.test/v1")
    assert str(OpenAICompatEmbedder.from_settings()._sdk.base_url).rstrip("/") == "http://embed.test/v1"

    sent = {}

    async def create(**kwargs):
        sent.update(kwargs)
        items = [SimpleNamespace(index=1, embedding=[0.0, 1.0]), SimpleNamespace(index=0, embedding=[1.0, 0.0])]
        return SimpleNamespace(data=items)

    monkeypatch.setattr(embedder._sdk.embeddings, "create", create)
    assert await embedder.embed(["a", "b"]) == [[1.0, 0.0], [0.0, 1.0]]  # input order, by index
    assert sent == {"model": settings.embed_model, "input": ["a", "b"], "encoding_format": "float"}


def test_startup_survives_qdrant_down(caplog):
    # The autouse no_real_kb fixture makes get_kb raise, like an unreachable Qdrant.
    caplog.set_level(logging.WARNING, logger="app.kb")
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
    assert any("knowledge base not indexed" in r.getMessage() for r in caplog.records)


async def test_startup_ingests(kb, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="app.kb")
    monkeypatch.setattr(kb.client, "delete_collection", lambda *a, **k: pytest.fail("unchanged: no rebuild"))
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
    messages = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("knowledge base unchanged") for m in messages)
    assert not any("not indexed" in m for m in messages)


@pytest.mark.parametrize(
    "reply", [lambda n: [[1.0, 0.0]] * (n - 1), lambda n: [[]] * n, lambda n: [[1.0]] + [[1.0, 0.0]] * (n - 1)]
)
async def test_bad_embedder_reply_never_wipes_the_index(kb, reply):
    async def bad_embed(texts):
        return reply(len(texts))

    kb.embedder.embed = bad_embed
    kb.embedder.model = "another-model"  # forces a rebuild
    assert await ingest(kb, DOCS) == {"status": "rebuilt", "mode": "sparse_only", "chunks": 24}
    assert (await kb.search("What does error 53300 mean?")).hits[0]["doc_id"] == "runbook-orders-db"


async def test_unknown_mode_and_zero_limit(kb):
    with pytest.raises(ValueError, match="unknown search mode"):
        await kb.search("Check payments-api", mode="bm25")
    assert (await kb.search("Check payments-api", limit=0)).hits == []


@pytest.mark.live
async def test_paraphrase_found_by_dense(live_embedder):
    # Shares no word of 4+ letters with runbook-orders-db / Database down.
    question = "The main Postgres node refuses all clients. How do we get writes working again?"
    section = next(c for c in load_chunks(DOCS) if c.section == "Database down")
    long_words = {w for w in sparse.tokens(section.indexed) if len(w) >= 4}
    assert long_words & set(sparse.tokens(question)) == set()
    kb = await fresh_kb(live_embedder)
    try:
        await ingest(kb, DOCS)
        search = await kb.search(question, mode="dense")
        found = [
            h["ranks"]["dense"]
            for h in search.hits
            if (h["doc_id"], h["section"]) == ("runbook-orders-db", "Database down")
        ]
        assert found and found[0] <= 3, [(h["doc_id"], h["section"]) for h in search.hits]
    finally:
        await kb.client.close()


# --- the search tool (T2) -------------------------------------------------------------------


async def search_call(tracer, store, query, *, id="c1", embed=None):
    call = {"id": id, "name": "search_knowledge_base", "args": {"query": query}}
    return await tool_gateway.execute(
        call, run_id="r1", decision=None, attempts_before=0, fault=None, store=store, tracer=tracer, embed=embed
    )


async def test_search_tool_returns_top3_with_ranks(kb, tracer, store):
    envelope, attempts = await search_call(tracer, store, "orders-db is down. Open an incident.")
    assert envelope["ok"] and attempts == 1 and "truncated" not in envelope
    data = envelope["data"]
    assert data["mode"] == "hybrid" and 1 <= len(data["results"]) <= 3
    for rank, hit in enumerate(data["results"], start=1):
        assert hit.keys() == {"doc_id", "title", "section", "snippet", "score", "ranks"}
        assert hit["ranks"].keys() == {"dense", "bm25", "rrf"} and hit["ranks"]["rrf"] == rank
        assert len(hit["snippet"]) <= 400 and hit["score"] == round(hit["score"], 4)
    again, _ = await search_call(tracer, store, "orders-db is down. Open an incident.", id="c2")
    assert again["data"] == data  # deterministic


async def test_snippet_is_the_first_400_characters(tracer, store, tmp_path, monkeypatch):
    long_text = "\n".join(f"Step {i}: restart the zebrafish relay and wait." for i in range(20))
    (tmp_path / "runbook-long.md").write_text(f"# Long runbook\n\n## Relay\n\n{long_text}\n")
    kb = await fresh_kb()
    try:
        await ingest(kb, tmp_path)
        monkeypatch.setattr(qdrant, "get_kb", lambda: kb)
        envelope, _ = await search_call(tracer, store, "zebrafish relay")
        [hit] = envelope["data"]["results"]
        assert len(long_text) > 400 and hit["snippet"] == long_text[:400]  # newlines kept
    finally:
        await kb.client.close()


async def test_search_emits_stage_events(kb, tracer, store, monkeypatch):
    await search_call(tracer, store, "What does error 53300 mean?", id="s1c0")
    events = [e for e in await store.list_events("r1") if e["kind"] == "stage"]
    assert [(e["node"], e["status"], e["tool"]) for e in events] == [
        ("kb.embed", "ok", "search_knowledge_base"),
        ("kb.dense", "ok", "search_knowledge_base"),
        ("kb.bm25", "ok", "search_knowledge_base"),
        ("kb.rrf", "ok", "search_knowledge_base"),
    ]
    assert all(e["data"]["tool_call_id"] == "s1c0" and e["attention"] is None for e in events)
    assert events[0]["data"]["model"] == "fake-embed"
    assert events[2]["data"]["ranking"][0]["doc_id"] == "runbook-orders-db"
    assert events[2]["data"]["ranking"][0].keys() == {"rank", "doc_id", "section", "score"}
    assert events[3]["data"]["ranking"][0]["ranks"]["rrf"] == 1

    bm25_only = await fresh_kb(FakeEmbedder(fail=True))
    try:
        await ingest(bm25_only, DOCS)
        monkeypatch.setattr(qdrant, "get_kb", lambda: bm25_only)
        envelope, _ = await search_call(tracer, store, "What does error 53300 mean?", id="s2c0")
        assert envelope["data"]["mode"] == "sparse_only"
        later = [
            e for e in await store.list_events("r1") if e["kind"] == "stage" and e["data"]["tool_call_id"] == "s2c0"
        ]
        assert [(e["node"], e["status"]) for e in later] == [
            ("kb.embed", "skipped"),
            ("kb.bm25", "ok"),
            ("kb.rrf", "ok"),
        ]
        [tool_event] = [
            e for e in await store.list_events("r1") if e["kind"] == "tool" and e["data"]["tool_call_id"] == "s2c0"
        ]
        assert tool_event["attention"] == "info"  # spec: search in sparse_only mode
    finally:
        await bm25_only.client.close()


async def test_embeddings_fault_gives_sparse_only(runner, search):
    llm = ScriptedLLM(
        [
            calls(("search_knowledge_base", {"query": "What does error 53300 mean?"}, "c0")),
            calls(("search_knowledge_base", {"query": "How do I promote the replica?"}, "c1")),
            final("done"),
        ]
    )
    run = await runner.create_run("orders-db errors", options={"faults": {"embeddings": {"mode": "error", "times": 1}}})
    assert await runner.run_segment(run["id"], llm_client=llm) == "completed"
    state = await runner.get_state(run["id"])
    results = [json.loads(m["content"]) for m in state["messages"] if m["role"] == "tool"]
    assert [r["data"]["mode"] for r in results] == ["sparse_only", "hybrid"]
    assert state["embed_attempts"] == 2
    events = await runner.store.list_events(run["id"])
    embeds = [(e["status"], e["data"]["reason"]) for e in events if e["node"] == "kb.embed"]
    assert embeds == [("failed", "injected fault"), ("ok", None)]
    tools = [e["attention"] for e in events if e["kind"] == "tool"]
    assert tools == ["info", None]


async def test_embed_counter_counts_every_search_attempt():
    counter = EmbedCounter(EmbeddingsFault(mode="error", times=2), attempts=1)
    assert [counter.next_fails() for _ in range(3)] == [True, False, False] and counter.attempts == 4
    assert EmbedCounter(None, 0).next_fails() is False


async def test_qdrant_down_gives_unavailable(kb, tracer, store, monkeypatch):
    async def down(*args, **kwargs):
        raise ConnectionError("qdrant refused the connection")

    monkeypatch.setattr(kb.client, "get_collection", down)
    envelope, attempts = await search_call(tracer, store, "Check payments-api")
    assert attempts == cfg.tool("search_knowledge_base").max_attempts == 3
    assert envelope["error"]["type"] == "unavailable" and envelope["error"]["retryable"]
    events = await store.list_events("r1")
    assert [e["kind"] for e in events] == ["tool", "retry", "tool", "retry", "tool"]
    assert events[-1]["attention"] == "error"


async def test_retried_search_attempt_embeds_and_counts_again(kb, tracer, store, monkeypatch):
    # The embed happens before Qdrant is queried, so a query failure that is retried at the
    # gateway makes `kb.search` run again, and it asks `fail_embed()` again each time.
    async def down(*args, **kwargs):
        raise ConnectionError("qdrant refused the connection")

    monkeypatch.setattr(kb.client, "query_points", down)
    embed = EmbedCounter(None, 0)
    envelope, attempts = await search_call(tracer, store, "Check payments-api", embed=embed)
    assert attempts == cfg.tool("search_knowledge_base").max_attempts == 3
    assert envelope["error"]["type"] == "unavailable"
    assert embed.attempts == 3  # one query-embedding attempt per gateway attempt


async def test_bm25_only_index_counts_no_embed_attempt(tracer, store, monkeypatch):
    bm25_only = await fresh_kb(FakeEmbedder(fail=True))
    try:
        await ingest(bm25_only, DOCS)
        monkeypatch.setattr(qdrant, "get_kb", lambda: bm25_only)
        embed = EmbedCounter(None, 0)
        envelope, _ = await search_call(tracer, store, "What does error 53300 mean?", embed=embed)
        assert envelope["data"]["mode"] == "sparse_only"
        assert embed.attempts == 0  # no dense index: nothing to embed, so fail_embed() is never called
    finally:
        await bm25_only.client.close()


@pytest.mark.parametrize("args", [{"query": "ab"}, {"query": "Check payments-api", "mode": "dense"}])
async def test_search_input_refuses_short_query_and_mode(tracer, store, args):
    call = {"id": "c1", "name": "search_knowledge_base", "args": args}
    envelope, attempts = await tool_gateway.execute(
        call, run_id="r1", decision=None, attempts_before=0, fault=None, store=store, tracer=tracer
    )
    assert envelope["ok"] is False and envelope["error"]["type"] == "validation" and attempts == 0


def test_nested_sparse_only_mode_gets_no_attention():
    # `_attention` only looks at the envelope's top-level `data`; a `mode: sparse_only` buried
    # inside another tool's result is not the search tool's own result and gets no attention.
    envelope = {"ok": True, "data": {"nested": {"mode": "sparse_only"}}}
    assert tool_gateway._attention("get_service_status", envelope) is None


async def test_two_searches_in_one_reply_share_the_node_embed_counter(runner, search):
    llm = ScriptedLLM(
        [
            calls(
                ("search_knowledge_base", {"query": "What does error 53300 mean?"}, "c0"),
                ("search_knowledge_base", {"query": "How do I promote the replica?"}, "c1"),
            ),
            final("done"),
        ]
    )
    run = await runner.create_run("orders-db errors", options={"faults": {"embeddings": {"mode": "error", "times": 1}}})
    assert await runner.run_segment(run["id"], llm_client=llm) == "completed"
    state = await runner.get_state(run["id"])
    results = [json.loads(m["content"]) for m in state["messages"] if m["role"] == "tool"]
    assert [r["data"]["mode"] for r in results] == ["sparse_only", "hybrid"]
    assert state["embed_attempts"] == 2  # one node run, one shared counter, two search calls

    events = await runner.store.list_events(run["id"])
    kb_stages = [e for e in events if e["kind"] == "stage" and e["node"].startswith("kb.")]
    for call_id in ("c0", "c1"):
        stage = [e for e in kb_stages if e["data"]["tool_call_id"] == call_id]
        assert stage and all(e["attention"] is None for e in stage)
    tools = [(e["data"]["tool_call_id"], e["attention"]) for e in events if e["kind"] == "tool"]
    assert tools == [("c0", "info"), ("c1", None)]


async def test_plain_objectives_through_fakeplanner_never_search_the_vendor_note(runner, search):
    # End to end (not just kb.search()): FakePlanner's own query, the real tool and the gateway
    # never surface the injected document for any plain objective. Some of these objectives
    # legitimately pause for a create_incident approval; that is unrelated to injection and is
    # covered by test_loop.py.
    for objective in PLAIN_OBJECTIVES:
        run = await runner.create_run(objective)
        await runner.run_segment(run["id"])
        state = await runner.get_state(run["id"])
        checked = 0
        for message in state["messages"]:
            if message["role"] != "tool":
                continue
            envelope = json.loads(message["content"])
            if envelope.get("ok") and isinstance(envelope["data"], dict) and "results" in envelope["data"]:
                doc_ids = [hit["doc_id"] for hit in envelope["data"]["results"]]
                assert "vendor-sms-note" not in doc_ids, objective
                checked += 1
        assert checked >= 1, objective  # a failed search would otherwise check nothing

    run = await runner.create_run(INJECTION_OBJECTIVE)
    assert await runner.run_segment(run["id"]) == "awaiting_approval"
    [approval] = [e for e in await runner.store.list_events(run["id"]) if e["kind"] == "approval"]
    assert approval["data"]["args"]["severity"] == "SEV1"


async def test_embedder_error_reported_by_the_tool(kb, tracer, store):
    kb.embedder = FakeEmbedder(fail=True)  # the index keeps its dense vectors
    envelope, _ = await search_call(tracer, store, "What does error 53300 mean?")
    assert envelope["data"]["mode"] == "sparse_only"
    [embed] = [e for e in await store.list_events("r1") if e["node"] == "kb.embed"]
    assert embed["status"] == "failed" and embed["data"]["reason"].startswith("ConnectionError: embedding endpoint")
    assert "kb.dense" not in [e["node"] for e in await store.list_events("r1")]


def test_top_n_above_three_fails_at_startup():
    with pytest.raises(ValueError):
        KB(top_n=4)
