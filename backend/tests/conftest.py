import asyncio
import logging
import math
import zlib

import pytest
from qdrant_client import AsyncQdrantClient

from app.config import cfg, settings
from app.eval import metrics
from app.eval.metrics import RagasJudge
from app.harness.runner import Runner
from app.harness.store import Store
from app.harness.tracer import Tracer
from app.kb import qdrant, sparse
from app.kb.ingest import ingest
from app.llm.openai_compat import OpenAICompatEmbedder

SECRET = "sk-test-secret-123"


@pytest.fixture
async def store(tmp_path):
    # A file, never :memory:, because the checkpointer opens a second connection to it.
    s = await Store.open(tmp_path / "harness.db")
    yield s
    await s.close()


@pytest.fixture
def tracer(store):
    return Tracer(store, [SECRET])


@pytest.fixture(autouse=True)
def restore_logging():
    """Tests may call log.setup(); put the root handlers back afterwards."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


@pytest.fixture(autouse=True)
def zero_retry_delay(monkeypatch):
    monkeypatch.setattr(cfg.retry, "base_delay_s", 0.0)
    monkeypatch.setattr(cfg.retry, "max_delay_s", 0.0)


@pytest.fixture
def short_timeouts(monkeypatch):
    """Tool and LLM timeouts of 0.05 s, for fault tests that wait for a real timeout.

    The search keeps its configured timeout: a real search emits four events and must not race 50 ms.
    """
    for name, tool in cfg.tools.items():
        if name != "search_knowledge_base":
            monkeypatch.setattr(tool, "timeout_s", 0.05)
    monkeypatch.setattr(cfg.llm, "timeout_s", 0.05)


# --- T6: runner ------------------------------------------------------------------------------


@pytest.fixture
async def runner(tmp_path, monkeypatch):
    """A runner on a file DB. The fake LLM and fault injection are on, whatever .env says."""
    monkeypatch.setattr(settings, "llm_default", "fake")
    monkeypatch.setattr(settings, "allow_fault_injection", True)
    r = await Runner.open(tmp_path / "harness.db")
    yield r
    await r.close()  # both connections: an open aiosqlite connection can hang the process


# --- M2 T1: knowledge base on in-memory Qdrant ----------------------------------------------


class FakeEmbedder:
    """Hashed bag of words: dense ranking in tests is word overlap. Only `live` tests show real semantics."""

    model = "fake-embed"
    dims = 256

    def __init__(self, fail: bool = False, delay: float = 0.0) -> None:
        self.fail, self.delay, self.calls = fail, delay, 0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise ConnectionError("embedding endpoint refused the connection")
        return [self._vector(t) for t in texts]

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dims
        for token in sparse.tokens(text):
            vector[zlib.crc32(token.encode()) % self.dims] += 1.0
        norm = math.sqrt(sum(v * v for v in vector))
        return [v / norm for v in vector] if norm else [1.0] + [0.0] * (self.dims - 1)


@pytest.fixture(autouse=True)
def no_real_kb(monkeypatch):
    def refuse():
        raise RuntimeError("no knowledge base in this test: use the kb fixture")

    monkeypatch.setattr(qdrant, "get_kb", refuse)


@pytest.fixture
async def kb(monkeypatch):
    """The fixture documents indexed in in-memory Qdrant with the fake embedder."""
    knowledge_base = qdrant.KnowledgeBase(AsyncQdrantClient(location=":memory:"), FakeEmbedder())
    await ingest(knowledge_base, settings.data_dir / "kb")
    monkeypatch.setattr(qdrant, "get_kb", lambda: knowledge_base)
    yield knowledge_base
    await knowledge_base.client.close()


@pytest.fixture
async def live_embedder():
    embedder = OpenAICompatEmbedder.from_settings()
    try:
        async with asyncio.timeout(5):
            await embedder.embed(["ping"])
    except Exception as exc:
        pytest.skip(f"no embedding endpoint: {type(exc).__name__}")
    return embedder


@pytest.fixture
def search(kb):
    """The real search_knowledge_base on the in-memory knowledge base."""
    return kb


# --- M2 T3: judge doubles -------------------------------------------------------------------


class FakeJudge:
    """Scores every metric 0.9 unless told otherwise; `calls` records (metric, args)."""

    model = "fake-judge"

    def __init__(self, scores: dict | None = None, reachable: bool = True, errors: dict | None = None) -> None:
        self.scores, self.is_reachable, self.errors = scores or {}, reachable, errors or {}
        self.calls: list[tuple[str, tuple]] = []

    async def reachable(self) -> bool:
        return self.is_reachable

    async def _score(self, metric: str, *args) -> float:
        self.calls.append((metric, args))
        if metric in self.errors:
            raise self.errors[metric]
        return self.scores.get(metric, 0.9)

    async def context_precision(self, question, reference, contexts):
        return await self._score("context_precision", question, reference, contexts)

    async def context_recall(self, question, reference, contexts):
        return await self._score("context_recall", question, reference, contexts)

    async def context_relevance(self, query, contexts):
        return await self._score("context_relevance", query, contexts)

    async def faithfulness(self, question, answer, contexts):
        return await self._score("faithfulness", question, answer, contexts)

    async def answer_relevancy(self, question, answer):
        return await self._score("answer_relevancy", question, answer)


@pytest.fixture
async def live_judge():
    judge = RagasJudge.from_settings()
    if not await judge.reachable():
        pytest.skip("no judge endpoint")
    return judge


@pytest.fixture(autouse=True)
def no_real_judge(monkeypatch):
    """No test reaches a real judge: create_run then stores evaluate=false. Online tests patch in their own judge."""
    monkeypatch.setattr(metrics, "get_judge", lambda: FakeJudge(reachable=False))
