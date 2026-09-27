# Design report

A short report on how the harness is built and why. The full contract is in [specs/ops-agent-harness.md](../specs/ops-agent-harness.md); each decision has an [ADR](adr/README.md).

## 1. Approach

The LLM only **proposes**: a final answer, or tool calls. The harness **decides** whether a call runs, how it runs, what happens when it fails, when the run stops, who must approve it, and what gets recorded.

A run goes through these steps:

1. A user gives an objective through the API, the CLI or the UI.
2. The harness asks the LLM what to do next, with the history and the tool schemas.
3. It checks the reply. A broken reply is repaired or ends the run.
4. It checks each proposed call: arguments, repeats, limits. A call to `create_incident` pauses the run until a person decides.
5. It runs the calls with a timeout, retries transient failures, validates the output, and gives the LLM a structured result.
6. It repeats from step 2 until the LLM answers or a limit is hit. Every step is saved and traced.

```
              ┌──────────────── Agent Harness  (backend/app/harness/) ────────┐
 API/CLI/UI ─►│ runner        run lifecycle: start, pause, resume, cancel      │
              │ loop          LangGraph: guard → agent → approval → tools      │
              │ llm_gateway   call the LLM, classify the reply, repair         │──► LLM (OpenAI-compatible | fake)
              │ tool_gateway  validate in/out, timeout, retry, idempotency     │──► tools (mocks + knowledge base)
              │ policy        limits, clamping, repeat guard                   │
              │ store/tracer  SQLite state and history, live events, JSON logs │
              └────────────────────────────────────────────────────────────────┘
```

LangGraph runs the graph, saves a checkpoint after every step and pauses at `interrupt()`. Every policy lives in our own nodes ([ADR 0002](adr/0002-agent-loop-langgraph-custom-nodes.md)).

```
START → guard ──limit hit────────────────────→ finalize → END
          │ ok
        agent ──final answer────────────────→ finalize
          │──malformed reply─────────────────→ guard
          │──needs approval──→ approval (pause) ──→ tools
          └──other tool calls───────────────→ tools ──→ guard
```

## 2. Technology stack

| Layer | Choice | Why | ADR |
|---|---|---|---|
| Backend | Python 3.12, FastAPI, uv, pytest, ruff | Async I/O, Pydantic validation and JSON Schema, fast tooling | [0001](adr/0001-backend-python-fastapi.md) |
| Agent loop | LangGraph `StateGraph`, own nodes | Checkpoints and `interrupt()` for approvals; policies stay in our code | [0002](adr/0002-agent-loop-langgraph-custom-nodes.md) |
| LLM | `openai` SDK against any OpenAI-compatible API; rule-based fake by default | Raw replies for exact checks; runs with no key | [0003](adr/0003-llm-openai-compatible-with-fake.md) |
| State and history | SQLite (`aiosqlite`, LangGraph `AsyncSqliteSaver`) | One file, transactions, survives restarts | [0004](adr/0004-state-and-database-sqlite.md) |
| Knowledge base search | Hybrid: dense + BM25, fused with RRF | Finds exact error codes and paraphrases | [0005](adr/0005-kb-search-hybrid-rag.md) |
| Vector store | Qdrant (Docker), no reranker | Dense and sparse vectors in one collection | [0006](adr/0006-vector-store-qdrant-no-rerank.md) |
| Embeddings | OpenAI-compatible `/embeddings`; BM25-only fallback | Local or cloud model by env vars | [0007](adr/0007-embeddings-api-sparse-fallback.md) |
| Evaluation | RAGAS + deterministic retrieval metrics | Offline golden set and per-run scores | [0008](adr/0008-evaluation-ragas-offline-online.md) |
| Observability | Own trace events (SQLite + Server-Sent Events), JSON logs | No extra infrastructure; the UI shows what ran | [0014](adr/0014-observability-trace-events.md) |
| UI | Angular, run console with approval inbox | Shows steps, state, approvals and budget live | [0015](adr/0015-ui-run-console-not-chat.md), [0016](adr/0016-ui-angular.md) |
| Process | AI-SDLC loop, GitHub Actions CI | Traceable requirements, two human gates | [0017](adr/0017-docs-postman-ci.md), [0018](adr/0018-adopt-ai-sdlc-workflow.md) |

## 3. Database design

One SQLite file (`DB_PATH`, default `data/harness.db`). LangGraph owns its checkpoint tables (`checkpoints`, `writes`): they hold each run's messages and counters and are the source of truth for resuming. Our tables hold what people and tools query ([ADR 0004](adr/0004-state-and-database-sqlite.md)).

```
runs 1 ──< approvals      one row per call that needed a decision
runs 1 ──< events         append-only trace, ordered by seq
runs 1 ──< evals          per-run quality scores
runs 1 ──< incidents      mock incident system (one per idempotency key)
eval_reports              golden-set runs, not tied to a run
```

| Table | Key columns | Purpose |
|---|---|---|
| `runs` | `id`, `objective`, `status`, `llm_mode`, `options_json`, `final`, `error`, `steps`, `tool_calls`, timestamps | One row per run: list, status, options for resume |
| `approvals` | `id`, `run_id`, `tool_call_id`, `tool`, `args_json`, `status`, `decision_json`, `reason`, `decided_by`, `expires_at` | Audit of human decisions. `UNIQUE(run_id, tool_call_id)`; decisions update only `pending` rows |
| `events` | `seq` (autoincrement), `run_id`, `t_ms`, `kind`, `node`, `tool`, `status`, `attention`, `msg`, `data_json` | Trace for the UI, the API and replays (`Last-Event-ID`). Index on `(run_id, seq)` |
| `evals` | `id`, `run_id`, `target`, `metric`, `value`, `judge_model`, `error` | Online scores: context relevance per search, faithfulness and answer relevancy of the answer |
| `eval_reports` | `id`, `created_at`, `models_json`, `config_json`, `summary_json`, `rows_json` | Offline golden-set reports, compared across runs |
| `incidents` | `id`, `idempotency_key` (unique), `run_id`, `title`, `description`, `severity`, `status` | The mock external system. A retried call returns the same incident |

Run statuses: `running`, `awaiting_approval`, `completed`, `failed`, `limit_exceeded`, `timed_out`, `cancelled`, `interrupted`. Approval statuses: `pending`, `approved`, `rejected`, `edited`, `expired`, `cancelled`.

The knowledge base lives in Qdrant, collection `ops_kb`: one point per runbook section with a `dense` vector, a `bm25` sparse vector (IDF applied by Qdrant) and payload `doc_id, title, section, text, content_hash, embed_model` ([ADR 0006](adr/0006-vector-store-qdrant-no-rerank.md)).

## 4. Safety controls

| Risk | Control |
|---|---|
| Wrong or harmful tool arguments | Pydantic input models reject unknown fields, wrong types, bad severities and service names; the tool does not run ([spec](../specs/ops-agent-harness.md#tools)) |
| Side effect without consent | `create_incident` always pauses for approve, edit or reject; the gateway also refuses it without a decision; a cancelled run closes its approvals ([ADR 0009](adr/0009-human-approval-interrupt.md)) |
| Duplicate side effects on retry | Idempotency key `run_id:tool_call_id`; incident cap per run |
| Endless loops | Step and tool-call limits, repeat guard, repair limit, time limit per segment, recursion limit ([ADR 0011](adr/0011-execution-limits.md)) |
| Flaky tools and models | Envelope results, retries only for transient errors, reply repair ([ADR 0010](adr/0010-failure-handling-envelope-retry-repair.md)) |
| Prompt injection in tool output | Tool output is untrusted data in the prompt; the approval gate is the hard stop |
| Clients asking for more | Limits clamped to `config.yaml`; fault injection only when `ALLOW_FAULT_INJECTION=true` (on by default for local use) |
| Leaking secrets | Secret values masked in logs and events; never returned by the API |

## 5. Observability

Every node step, LLM call, tool attempt, retry, approval, evaluation and state-changing API call emits an event `{seq, run_id, t_ms, kind, node, tool, status, attention, msg, data}`. Events are stored in `events`, streamed live (`GET /api/runs/{id}/events`), exported (`GET /api/runs/{id}/trace`) and written to stdout as JSON log lines with `run_id`. `attention` marks what a person should look at; the UI turns it into colour, icon and text ([ADR 0014](adr/0014-observability-trace-events.md)).

## 6. API

The routes, request bodies, status codes and errors are defined once, in the [spec's API table](../specs/ops-agent-harness.md#api). [docs/postman_collection.json](postman_collection.json) has a ready request for each route and the create → approve → trace flow.

## 7. Configuration

Two sources ([backend/app/config.py](../backend/app/config.py)):

- **`config.yaml`**, behaviour, versioned with the code: limits, retries, timeouts per tool, search, approval TTL, output size, evaluation. Unknown keys stop the app at startup.
- **Environment variables** (or `.env`, see [.env.example](../.env.example)), deployment: endpoints, keys, paths. Relative paths are resolved from the repo root.

| Variable | Default | Purpose |
|---|---|---|
| `LLM_DEFAULT` | `fake` | LLM mode when a run does not choose one: `fake` or `openai` |
| `LLM_BASE_URL` | `http://localhost:1234/v1` | OpenAI-compatible chat endpoint (LM Studio by default) |
| `LLM_API_KEY` | `lm-studio` | Key for that endpoint |
| `LLM_MODEL` | `qwen/qwen3.5-9b` | Chat model; must support tool calling |
| `EMBED_BASE_URL` | empty (reuse `LLM_BASE_URL`) | Embeddings endpoint |
| `EMBED_API_KEY` | empty (reuse `LLM_API_KEY`) | Key for embeddings |
| `EMBED_MODEL` | `text-embedding-bge-m3` | Embedding model; changing it rebuilds the collection |
| `JUDGE_BASE_URL` | empty (reuse `LLM_BASE_URL`) | RAGAS judge endpoint |
| `JUDGE_API_KEY` | empty (reuse `LLM_API_KEY`) | Key for the judge |
| `JUDGE_MODEL` | empty (reuse `LLM_MODEL`) | Judge model; use a strong one for numbers you report |
| `JUDGE_JSON_MODE` | `json_schema` | How the judge is forced to return JSON: `json_schema`, `json`, `md_json`, `tools` |
| `QDRANT_URL` | `http://localhost:6333` | Qdrant server |
| `QDRANT_API_KEY` | empty | Qdrant Cloud key |
| `DB_PATH` | `data/harness.db` | SQLite file for state, history and the mock incident system |
| `LOG_LEVEL` | `INFO` | Log level |
| `LOG_FORMAT` | `json` | `json` (one object per line) or `text` |
| `ALLOW_FAULT_INJECTION` | `true` | Accept `faults` in run requests. Set `false` in any shared deployment |
| `APPROVER_TOKEN` | empty | When set, approval decisions over HTTP need header `X-Approver-Token` |
| `CORS_ORIGINS` | `http://localhost:4200` | Comma-separated origins allowed to call the API (UI in development) |
| `DATA_DIR` | `data` | Fixtures (`kb/`, `services.json`) |
| `CONFIG_PATH` | `config.yaml` | Behaviour file |

## 8. Limitations

- **One process.** Runs execute as background tasks in the API process, and SQLite has one writer. There is no horizontal scaling.
- **No authentication.** Anyone who can reach the API can start runs and decide approvals; `APPROVER_TOKEN` is the only guard.
- **Fault injection is on by default.** Convenient for demos; any shared deployment must set `ALLOW_FAULT_INJECTION=false`.
- **Mock tools.** The incident system, service status and knowledge base are fixtures. The knowledge base is small and in English.
- **Sequential tool calls.** Calls in one reply run one after another.
- **Time limit per segment.** `max_run_seconds` counts each start or resume separately; waiting for an approval is not counted.
- **No token or cost budget.** Token usage is recorded, not limited.
- **Search needs services.** Hybrid search needs Qdrant and an embedding endpoint; without embeddings it falls back to BM25. There is no reranker.
- **Prompt injection is only contained, not detected.** The approval gate stops the only side effect; nothing flags injected text.
- **Fake LLM.** `FakePlanner` follows fixed rules to show the harness mechanics, not model quality. Some local models write tool calls as text; those are not parsed.
- **Evaluation quality.** RAGAS scores depend on the judge model; the golden set has about 15 questions.
- **Manual recovery.** Runs interrupted by a crash wait for someone to resume them.

## 9. Future improvements

- PostgreSQL checkpointer and a worker queue, so several workers can run and resume runs.
- Authentication, roles for requesters and approvers, and policy-based approval (for example automatic approval for low severities).
- OpenTelemetry export of the trace events, and LLM-focused tracing (Langfuse or LangSmith).
- Token and cost budgets per run.
- Parallel execution of calls that have no side effects.
- Semantic loop detection (similar, not only identical, calls).
- Prompt-injection detection on tool output before it reaches the LLM.
- Cross-encoder reranking and a multilingual knowledge base.
- A larger golden set and end-to-end agent evaluation (right tool, right order, step count).
- Tools exposed through MCP; streaming LLM tokens to the UI.
- Follow-up objectives in the same run (multi-turn).
- Rate limiting per client.
- Automatic resume of interrupted runs when the pending step has no side effect.
