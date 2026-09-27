"""Offline evaluation on the golden set (spec: Evaluation, Offline).

Every question runs in each mode. Retrieval metrics are deterministic;
RAGAS context precision and recall run only when the judge is reachable.
The report is stored in `eval_reports` and returned.
"""

import json
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from pydantic import Field

from app.clock import now_iso
from app.config import ROOT, Strict, cfg
from app.eval.metrics import doc_ranking, hit_at_k, reason, recall_at_k, reciprocal_rank, safe_score

MODES = ("hybrid", "dense", "sparse")


class GoldenItem(Strict):
    question: str = Field(min_length=3)
    reference: str = Field(min_length=3)
    relevant_doc_ids: list[str] = Field(min_length=1)


def load_golden(path: Path | None = None) -> list[GoldenItem]:
    path = path or ROOT / cfg.eval.golden_set
    lines = path.read_text(encoding="utf-8").splitlines()
    return [GoldenItem.model_validate(json.loads(line)) for line in lines if line.strip()]


async def run_golden(
    kb,
    judge,
    store,
    *,
    modes: tuple[str, ...] = MODES,
    progress: Callable[[int, int], Awaitable[None]] | None = None,
    golden: list[GoldenItem] | None = None,
) -> dict:
    items = golden if golden is not None else load_golden()
    reachable = await judge.reachable()  # probed once: a judge that is down is not asked per question
    rows, done, total = [], 0, len(items) * len(modes)
    for mode in modes:
        for item in items:
            rows.append(await _row(kb, judge, reachable, mode, item))
            done += 1
            if progress is not None:
                await progress(done, total)
    report = {
        "id": uuid.uuid4().hex,
        "created_at": now_iso(),
        "models": {"embed_model": kb.embedder.model, "judge_model": judge.model},
        "config": {
            "golden_set": cfg.eval.golden_set,
            "modes": list(modes),
            "kb": cfg.kb.model_dump(),
            "bm25": cfg.bm25.model_dump(),
        },
        "summary": {mode: _summary([r for r in rows if r["mode"] == mode], reachable) for mode in modes},
        "rows": rows,
    }
    await store.insert_eval_report(report)
    return report


async def _row(kb, judge, reachable: bool, mode: str, item: GoldenItem) -> dict:
    row = {
        "mode": mode,
        "question": item.question,
        "relevant_doc_ids": item.relevant_doc_ids,
        "retrieved_doc_ids": [],
        "mode_used": None,
        "hit@3": None,
        "rr@10": None,
        "recall@3": None,
        "context_precision": None,
        "context_recall": None,
        "judge_error": None if reachable else "judge unreachable",
        "error": None,
    }
    try:
        search = await kb.search(item.question, mode=mode, limit=10)
    except Exception as exc:  # e.g. dense mode on a BM25-only index: recorded, left out of the means
        row["error"] = reason(f"{type(exc).__name__}: {exc}")
        return row
    ranking = doc_ranking(search.hits)
    relevant = item.relevant_doc_ids
    row |= {
        "retrieved_doc_ids": ranking,
        "mode_used": search.mode,
        "hit@3": hit_at_k(ranking, relevant, 3),
        "rr@10": reciprocal_rank(ranking, relevant, 10),
        "recall@3": recall_at_k(ranking, relevant, 3),
    }
    if not reachable:
        return row
    contexts = [hit["text"] for hit in search.hits[:3]]
    if not contexts:
        row["judge_error"] = "no contexts"
        return row
    # One call at a time: a local judge serves one request at a time.
    precision, precision_error = await safe_score(judge.context_precision(item.question, item.reference, contexts))
    recall, recall_error = await safe_score(judge.context_recall(item.question, item.reference, contexts))
    row |= {"context_precision": precision, "context_recall": recall, "judge_error": precision_error or recall_error}
    return row


def _summary(rows: list[dict], reachable: bool) -> dict:
    ok = [r for r in rows if r["error"] is None]

    def mean(key: str) -> float | None:
        values = [r[key] for r in ok if r[key] is not None]
        return round(sum(values) / len(values), 4) if values else None

    return {
        "questions": len(rows),
        "errors": len(rows) - len(ok),
        "hit@3": mean("hit@3"),
        "mrr@10": mean("rr@10"),
        "recall@3": mean("recall@3"),
        "context_precision": mean("context_precision"),
        "context_recall": mean("context_recall"),
        "judge_error": None if reachable else "judge unreachable",
    }
