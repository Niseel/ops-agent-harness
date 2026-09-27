# Plan: M2 knowledge base and evaluation   (from [specs/ops-agent-harness.md](../specs/ops-agent-harness.md))

Status: draft (gate 1 before the milestone starts). Branch: `feat/m2-kb-and-eval`. PR title: `feat: M2 knowledge base search and evaluation`.

The real `search_knowledge_base` (hybrid dense + BM25 on Qdrant, fused with RRF) and quality measurement with RAGAS, offline on a golden set and online after each run. HTTP endpoints for evaluation arrive in M3.

## Files that change
- `backend/pyproject.toml` (edit) - add `qdrant-client`; `ragas`, `instructor`, `langchain-community<0.4` for T2
- `backend/app/kb/sparse.py`, `qdrant.py`, `ingest.py` (new) - BM25 vectors, Qdrant collection and search, chunk and index
- `backend/app/tools/kb.py` (new) - the tool: embed, dense ∥ BM25, RRF, `mode`, `stage` events
- `backend/app/llm/openai_compat.py` (edit) - embeddings client (`EMBED_*`)
- `backend/app/main.py` (edit) - ingest at startup; API still starts if Qdrant is down
- `data/kb/*.md` (new) - runbooks for the fixture services, severity policy, one injected-instruction document
- `backend/app/eval/metrics.py`, `golden.py`, `online.py` (new) - hit@k, MRR, recall, RAGAS judge, reports, per-run scores
- `backend/app/harness/loop.py` (edit) - `finalize` queues online evaluation
- `evals/kb_golden.jsonl` (new) - about 15 questions with reference answers and relevant doc ids
- `backend/tests/test_kb.py`, `test_eval.py` (new), `test_loop.py` (edit: real tool with in-memory Qdrant)

## Order of work
1. T1 → T2 → T3.

## Tasks
| ID | Task | Files | Skills | Depends on | Parallel | Est. |
|----|------|-------|--------|------------|----------|------|
| T1 | Hybrid knowledge base: fixtures, ingest with hash check, dense + BM25 + RRF search, `sparse_only` fallback (also forced by the `embeddings` fault), tool and events (AC-14, AC-2, AC-12) | kb/*, tools/kb.py, data/kb/, main.py | ai-engineer | M1 | no | 2.5h |
| T2 | Golden-set evaluation: deterministic metrics per mode, RAGAS context precision and recall, `eval_reports` (AC-15) | eval/metrics.py, eval/golden.py, evals/kb_golden.jsonl | ai-engineer | T1 | no | 1.5h |
| T3 | Online evaluation after each run: context relevance per search, faithfulness, answer relevancy, thresholds, judge-down handling (AC-15) | eval/online.py, harness/loop.py | ai-engineer | T2 | no | 1.5h |

## Risks
- `qdrant-client` in-memory mode may not apply `Modifier.IDF` like the server. If it does not, BM25 tests run against a Qdrant service container (CI) and are skipped locally without Docker.
- `ragas` 0.4 API: confirm `ContextRelevance` exists in `ragas.metrics.collections`; if not, use `LLMContextPrecisionWithoutReference` with the final answer (spec: Evaluation).
- `ragas` pulls a large dependency tree; keep its import lazy so the API starts fast and tests without a judge stay light.
- `FakePlanner` follows injected instructions found in search results. The injected document must rank in the top 3 only for its own scenario's query, or plain demo runs would pause for approval.
- The embedding dimension decides the collection shape; read it from the first embedding instead of hard-coding it.

## Proof
| AC | Tests |
|----|-------|
| AC-2 | `test_loop.py::test_success_run_completes` (now with the real tool and in-memory Qdrant) |
| AC-14 | `test_kb.py::test_rrf_fuses_ranks`, `::test_exact_term_found_by_bm25`, `::test_sparse_only_when_embeddings_down`, `::test_embeddings_fault_gives_sparse_only`, `::test_injected_doc_not_in_top3_for_payments_query`, `::test_ingest_skips_unchanged`, `::test_paraphrase_found_by_dense` (`live`) |
| AC-15 | `test_eval.py::test_hit_at_k_and_mrr`, `::test_golden_report_has_all_modes`, `::test_online_scores_stored_after_run`, `::test_low_score_emits_warn`, `::test_judge_down_gives_null_and_run_unchanged` |

## Pipeline log
| Phase | Result | Notes |
|-------|--------|-------|
