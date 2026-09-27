# 0008. Evaluation: RAGAS offline (golden set) and online (per run)

Status: Accepted · Date: 2026-09-26 · Golden set moved to `evals/kb_golden.jsonl`, see [0018](0018-adopt-ai-sdlc-workflow.md) · Refined in M2: online evaluation runs in the runner after `done`; the `evaluate` default needs a reachable judge; context relevance below its threshold also warns ([spec, Evaluation](../../specs/ops-agent-harness.md#evaluation))

## Context

`search_knowledge_base` returns context, not an answer. Quality has two layers:

- **Retrieval**: did the search return the right sections?
- **Grounding**: is the agent's final answer supported by what the tools returned?

## Options

| Option | Pros | Cons |
|---|---|---|
| Offline golden set only | Measures retrieval against human references. Compares modes. | Says nothing about a given run. |
| Online per run only | A score on every run. | No reference, so no recall. Cannot compare modes. |
| Both | Covers both layers. | About 3 hours more work. |

## Decision

Both.

**Offline, golden set** (`data/eval/kb_golden.jsonl`, lines of `{question, reference, relevant_doc_ids}`):

- Every question runs in three modes: `hybrid`, `dense`, `sparse`.
- Deterministic metrics, no LLM, run in CI: `hit@3`, `MRR@10`, `recall@k` by `doc_id`.
- RAGAS metrics with a judge LLM: `ContextPrecision`, `ContextRecall`.
- Each run is saved to `eval_reports`. Started from `POST /api/eval/kb` or `cli eval`.

**Online, per run** (option `evaluate`, on by default when a judge is reachable). A background job runs after the run finishes:

- Context relevance for every search call (query vs returned chunks).
- Faithfulness of the final answer against all tool outputs of the run (knowledge base and service status).
- Answer relevancy of the final answer to the objective.

Scores go to `evals` and to the event stream. Faithfulness below `eval.thresholds.faithfulness` raises a warning event.

**Judge**: `JUDGE_*` env vars, defaulting to `LLM_*`. If the judge is unreachable, the RAGAS metric is `null` with a reason. The deterministic metrics still work, and the run itself is never affected.

## Consequences

- The numbers back [0005](0005-kb-search-hybrid-rag.md) and show regressions when chunking or models change.
- Scores depend on the judge. Small local judges are slow and noisy; use a stronger model for numbers you report.
- The golden set is small (about 15 questions). It shows direction, not statistical significance.
