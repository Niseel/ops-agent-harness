import asyncio
import logging
import re
import shutil
from types import SimpleNamespace

import pytest
from conftest import FakeEmbedder
from fastapi.testclient import TestClient
from qdrant_client import AsyncQdrantClient, models

from app.config import cfg, settings
from app.kb import qdrant, sparse
from app.kb.ingest import content_hash, ingest, load_chunks
from app.kb.qdrant import KnowledgeBase, get_kb, rrf
from app.llm.openai_compat import OpenAICompatEmbedder
from app.main import app

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
