"""Retrieval metrics and the RAGAS judge (spec: Evaluation, ADR 0008).

The judge imports ragas and instructor only when it scores for the first time:
the ragas tree takes seconds to import, and the API, the runner and the tools
never need it.
"""

import asyncio
import logging
import math
import os
from collections.abc import Awaitable
from functools import cache

from openai import AsyncOpenAI

from app.config import cfg, settings
from app.llm.openai_compat import OpenAICompatEmbedder

log = logging.getLogger("app.eval")

METRICS = ("context_precision", "context_recall", "context_relevance", "faithfulness", "answer_relevancy")


def doc_ranking(hits: list[dict]) -> list[str]:
    """The hits' doc ids, best first, each once (a document can have several sections in the hits)."""
    return list(dict.fromkeys(hit["doc_id"] for hit in hits))


def hit_at_k(ranking: list[str], relevant: list[str], k: int) -> float:
    return 1.0 if set(ranking[:k]) & set(relevant) else 0.0


def reciprocal_rank(ranking: list[str], relevant: list[str], k: int) -> float:
    return next((1 / position for position, doc in enumerate(ranking[:k], start=1) if doc in relevant), 0.0)


def recall_at_k(ranking: list[str], relevant: list[str], k: int) -> float:
    return len(set(ranking[:k]) & set(relevant)) / len(set(relevant))


def reason(text: str) -> str:
    """A null metric's reason as stored: secret values masked, at most 200 characters."""
    for secret in settings.secrets():
        text = text.replace(secret, "***")
    return text[:200]


async def safe_score(call: Awaitable[float]) -> tuple[float | None, str | None]:
    """(value, None), or (None, reason) when the metric raises or has no value. CancelledError propagates."""
    try:
        value = await call
        if value is None or math.isnan(value):
            return None, "no value (NaN)"
        return float(value), None
    except Exception as exc:  # a value that is not a number lands here too
        log.warning("judge metric failed: %s: %s", type(exc).__name__, exc)
        return None, reason(f"{type(exc).__name__}: {exc}")


class RagasJudge:
    """RAGAS metrics scored by an OpenAI-compatible judge model (`JUDGE_*`, empty values reuse `LLM_*`)."""

    def __init__(self, *, base_url: str, api_key: str, model: str, json_mode: str) -> None:
        self.model = model
        self.json_mode = json_mode
        self._client = AsyncOpenAI(
            base_url=base_url, api_key=api_key or "none", max_retries=0, timeout=cfg.llm.timeout_s
        )
        self._metrics: dict | None = None

    @classmethod
    def from_settings(cls) -> "RagasJudge":
        return cls(
            base_url=settings.judge_base_url or settings.llm_base_url,
            api_key=settings.judge_api_key or settings.llm_api_key,
            model=settings.judge_model or settings.llm_model,
            json_mode=settings.judge_json_mode,
        )

    async def reachable(self) -> bool:
        """True when the judge answers GET /models within 2 s."""
        try:
            await self._client.with_options(timeout=2.0, max_retries=0).models.list()
            return True
        except Exception:
            return False

    def metrics(self) -> dict:
        """Build the five RAGAS metrics once. Imports ragas here, not at module import."""
        if self._metrics is None:
            os.environ.setdefault("RAGAS_DO_NOT_TRACK", "true")  # ragas sends usage analytics otherwise
            import instructor
            from ragas.llms.base import InstructorLLM
            from ragas.metrics import collections as ragas_metrics

            # Not ragas' llm_factory: it always uses Mode.JSON and would ignore JUDGE_JSON_MODE.
            patched = instructor.from_openai(self._client, mode=instructor.Mode[self.json_mode.upper()])
            llm = InstructorLLM(client=patched, model=self.model, provider="openai")
            self._metrics = {
                "context_precision": ragas_metrics.ContextPrecision(llm=llm),
                "context_recall": ragas_metrics.ContextRecall(llm=llm),
                "context_relevance": ragas_metrics.ContextRelevance(llm=llm),
                "faithfulness": ragas_metrics.Faithfulness(llm=llm),
                "answer_relevancy": ragas_metrics.AnswerRelevancy(
                    llm=llm, embeddings=_ragas_embeddings(), strictness=cfg.eval.relevancy_strictness
                ),
            }
        return self._metrics

    async def _metric(self, name: str):
        if self._metrics is None:
            # The first build imports ragas (seconds): off the event loop, so other runs and streams keep going.
            await asyncio.to_thread(self.metrics)
        return self._metrics[name]

    # Only ascore(): ragas' sync score() and evaluate() apply nest_asyncio to the running loop.
    async def context_precision(self, question: str, reference: str, contexts: list[str]) -> float:
        m = await self._metric("context_precision")
        return (await m.ascore(user_input=question, reference=reference, retrieved_contexts=contexts)).value

    async def context_recall(self, question: str, reference: str, contexts: list[str]) -> float:
        m = await self._metric("context_recall")
        return (await m.ascore(user_input=question, retrieved_contexts=contexts, reference=reference)).value

    async def context_relevance(self, query: str, contexts: list[str]) -> float:
        m = await self._metric("context_relevance")
        return (await m.ascore(user_input=query, retrieved_contexts=contexts)).value

    async def faithfulness(self, question: str, answer: str, contexts: list[str]) -> float:
        m = await self._metric("faithfulness")
        return (await m.ascore(user_input=question, response=answer, retrieved_contexts=contexts)).value

    async def answer_relevancy(self, question: str, answer: str) -> float:
        m = await self._metric("answer_relevancy")
        return (await m.ascore(user_input=question, response=answer)).value


def _ragas_embeddings():
    """Our embedder in the shape ragas' AnswerRelevancy accepts. Defined here to keep ragas out of module import."""
    from ragas.embeddings.base import BaseRagasEmbedding

    class Embeddings(BaseRagasEmbedding):
        def __init__(self, embedder: OpenAICompatEmbedder) -> None:
            super().__init__()
            self.embedder = embedder

        def embed_text(self, text: str, **kwargs) -> list[float]:
            raise NotImplementedError("the judge only uses the async methods")

        async def aembed_text(self, text: str, **kwargs) -> list[float]:
            return (await self.embedder.embed([text]))[0]

        async def aembed_texts(self, texts: list[str], **kwargs) -> list[list[float]]:
            return await self.embedder.embed(texts)

    return Embeddings(OpenAICompatEmbedder.from_settings())


@cache
def get_judge() -> RagasJudge:
    """One judge per process, from settings. Tests replace it with a fake."""
    return RagasJudge.from_settings()
