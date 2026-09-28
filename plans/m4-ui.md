# Plan: M4 run console UI   (from [specs/ops-agent-harness.md](../specs/ops-agent-harness.md))

Status: approved (gate 1, 2026-09-28). Branch: `feat/m4-ui`. PR title: `feat: M4 run console UI`.

The web UI from [ADR 0015](../docs/adr/0015-ui-run-console-not-chat.md) and [ADR 0016](../docs/adr/0016-ui-angular.md): start runs, watch them live, decide approvals, see what needs attention, compare search modes. It is the smallest UI that meets the spec's UI section and AC-13. It reads the M3 API as it is, plus one additive change: the `llm` event lists the proposed calls with their arguments, so the NOW bar can show a call before its first attempt ends.

## Files that change
- `frontend/` (new, T1) - Angular 22.2 workspace from `ng new`: `package.json`, `package-lock.json`, `angular.json`, `tsconfig*.json`, `.prettierrc`, `.editorconfig`, `.gitignore`, `public/`, `src/index.html`, `src/main.ts`, `src/app/app.config.ts`; plus `proxy.conf.json` (`/api` → :8000)
- `frontend/src/styles.css` (new, T1) - the only stylesheet: tokens, light and dark, layout, attention colours
- `frontend/src/app/app.ts`, `app.spec.ts` (new T1; edit T3, T6) - shell with the three tabs
- `frontend/src/app/api.ts` (new T1; edit T3, T6), `api.spec.ts` (new T1; edit T3, T6) - fetch helper, types, `followRun`, `readSse`
- `frontend/src/app/trace.ts`, `trace.spec.ts`, `trace.fixture.json` (new, T2) - `TraceStore`; the fixture is a real run's trace (Approve scenario with two status timeouts), read by the spec; `frontend/tsconfig.spec.json` gains `resolveJsonModule` for it
- `frontend/src/app/runs-page.ts`, `runs-page.html` (new T3; edit T4, T5) - the Runs tab
- `frontend/src/app/run-form.ts`, `run-form.spec.ts` (new, T3)
- `frontend/src/app/flow.ts`, `flow-diagram.ts`, `flow-diagram.spec.ts` (new, T4)
- `frontend/src/app/approval-inbox.ts`, `approval-inbox.spec.ts` (new, T5)
- `frontend/src/app/eval-tab.ts`, `incidents-tab.ts` (new, T6)
- `backend/app/harness/llm_gateway.py`, `backend/tests/test_llm_gateway.py` (edit, T2) - `llm` event lists calls with `args`
- `CLAUDE.md`, `README.md` (edit, T1); `specs/ops-agent-harness.md` (edit, T2); `docs/DESIGN.md` (edit; T2, T3, T5); `docs/REVIEW_GUIDE.md` (edit; T1, T5) - exact notes in Doc changes

## Order of work
1. T1 → T2 → T3 → T4 → T5 → T6. One commit each; stop after each commit for review.
2. T6 needs only T1, so it may go anywhere after T1. T4 and T5 both need T3, not each other.

## Tasks
| ID | Task | Files | Skills | Depends on | Parallel | Est. |
|----|------|-------|--------|------------|----------|------|
| T1 | Scaffold: `ng new` with pinned options, dev proxy, analytics off; one stylesheet (tokens, light and dark, layout, attention colours); shell with tabs Runs, Evaluation, Incidents; `api.ts` (fetch helper, `ApiError`, `detailText`, types); frontend commands | frontend/ (scaffold, proxy.conf.json, styles.css, app.ts, app.spec.ts, api.ts), CLAUDE.md, README.md, docs/REVIEW_GUIDE.md | - | M3 | no | 0.5h |
| T2 | `TraceStore`: events → timeline, NOW bar, node states, active node, attention list, budget used; `attentionStyle`, `resultText`. Backend: the `llm` event lists calls with `args` (AC-13) | trace.ts, trace.spec.ts, harness/llm_gateway.py, tests/test_llm_gateway.py, specs/ops-agent-harness.md, docs/DESIGN.md | ai-engineer | T1 | no | 1.25h |
| T3 | Runs tab, left and center: run form (objective, LLM mode, fault switches, limits, evaluate), runs list, opening a run (`followRun`: live events, closed after `done`), refresh loop, timeline with results and evaluation badges, detail panel (AC-13) | runs-page.ts/.html, run-form.ts, run-form.spec.ts, api.ts, api.spec.ts, app.ts, docs/DESIGN.md | - | T2 | no | 1.25h |
| T4 | Runs tab, right and bottom: budget meters, attention list, NOW bar, flow diagram (SVG drawn from `flow.ts`), console with filters (AC-13) | flow.ts, flow-diagram.ts, flow-diagram.spec.ts, runs-page.ts/.html | - | T3 | yes (with T5) | 1h |
| T5 | Approval inbox for all runs: TTL countdown, approve, edit, reject with a reason, approver token setting; the UI check in the review guide (AC-13) | approval-inbox.ts, approval-inbox.spec.ts, runs-page.ts/.html, docs/DESIGN.md, docs/REVIEW_GUIDE.md | - | T3 | yes (with T4) | 0.75h |
| T6 | Evaluation tab (latest report, a new run over `fetch` SSE with progress, modes compared) and Incidents tab | eval-tab.ts, incidents-tab.ts, api.ts, api.spec.ts, app.ts | - | T1 | yes | 0.75h |

The draft had 6 tasks (7.5h). Its T1 mixed the scaffold with the store and the renderer, and all specs sat in a last task. Now the scaffold is small, the store is its own task, and each task carries its own tests. The estimate drops by 2h to 5.5h: no router, no `HttpClient`, no Angular forms, no theme toggle, no health indicator (see "Not built").

## Interfaces
Names that later tasks and M5 rely on. Exact signatures are the coder's choice.

| Module (task) | Exposes | Does |
|---|---|---|
| `api.ts` (T1) | `api<T>(method, path, body?, headers?)`, `ApiError` (`status`, `detail`), `detailText(detail)`, types `TraceEvent`, `RunSummary`, `RunDetail`, `Approval`, `Incident`, `EvalReport` | "General", "Scaffold" |
| `api.ts` (T3, T6) | `followRun(runId, onEvent) → close()` (T3), `readSse(body, onEvent)` (T6) | "Runs tab", "Evaluation and Incidents tabs" |
| `trace.ts` (T2) | `TraceStore`: `add`, `events`, `timeline`, `now`, `nodes`, `activeNode`, `attention`, `used`, `expectedEvals(hasFinal)`; functions `nodeOf`, `attentionStyle`, `resultText` | "TraceStore" |
| `run-form.ts` (T3) | `RunForm` (output `created`), `runBody(values)` | "Runs tab" |
| `runs-page.ts` (T3) | `RunsPage` | "Runs tab" |
| `flow.ts`, `flow-diagram.ts` (T4) | `FLOW`, `FlowDiagram` (inputs `nodes`, `active`) | "Runs tab" |
| `approval-inbox.ts` (T5) | `ApprovalInbox` (input `approvals`; outputs `decided`, `openRun`) | "Approval inbox" |
| `eval-tab.ts`, `incidents-tab.ts` (T6) | `EvalTab`; `IncidentsTab` (input `active`) | "Evaluation and Incidents tabs" |
| `harness/llm_gateway.py` (T2) | `llm` event `data.tool_calls[i]` = `{id, name, args}` | "Backend" |

## Rules pinned by this plan
Taken from the spec, ADRs 0014–0016, the M2 and M3 handoffs, the backend code at `3800a53` and the library notes, so the coder does not have to decide.

**General**
- Angular 22.2: standalone components, signals, zoneless (no zone.js). No router, no `HttpClient`, no Angular forms: `fetch`, a native `<form>` read with `FormData`, native `<details>`, `<meter>` and `<progress>`. No npm dependency beyond what `ng new` adds.
- Component state lives in signals only. Angular 22 components are OnPush by default and the app is zoneless, so a plain field changed in an `EventSource`, `fetch` or timer callback never re-renders.
- Components are single `.ts` files with inline templates; only the Runs tab has a separate `runs-page.html`. No component styles: `src/styles.css` holds all CSS.
- URLs are relative (`/api/...`): the dev proxy in development, the same origin when FastAPI serves the build (M5). Path segments go through `encodeURIComponent`.
- Untrusted text (objectives, LLM output, tool results, knowledge-base snippets with injected text) is shown only through interpolation or the `json` pipe. No `innerHTML`, no `bypassSecurityTrust*`.
- Attention is never colour alone: each highlighted item has its icon (`aria-hidden="true"`) and a visible word. Every input has a label.
- Server times are shown with `Date` (`toLocaleTimeString`); no date library.
- A failed request shows its `detailText` in the panel that made it, and the panel keeps its last data.

**Scaffold** (T1)
- From the repo root: `NG_CLI_ANALYTICS=false npx @angular/cli@22.2.0 new frontend --routing=false --style=css --ssr=false --zoneless --test-runner=vitest --ai-config=none --skip-git --package-manager=npm --inline-template --inline-style`. Every option is given, so nothing prompts. `--skip-git`: the repo exists. `--ai-config=none`: no agent instruction files under `frontend/`.
- Delete the generated `frontend/README.md` and `frontend/.vscode/`. Run `npx prettier --write src` once. Log `node --version` and `npm --version` in the Pipeline log.
- `angular.json`: `"cli": {"analytics": false}`; `serve.options.proxyConfig: "proxy.conf.json"`. `proxy.conf.json`: `{"/api/**": {"target": "http://localhost:8000", "secure": false}}`.
- Stylesheet: custom properties on `:root` (background, text, muted, border, panel, and the attention colours amber, orange, red, blue, green); dark values under `@media (prefers-color-scheme: dark)`; `color-scheme: light dark`. Classes `.amber`, `.orange`, `.red`, `.blue`, `.green` apply them. Layout: ADR 0015's sketch as a CSS grid (left, center, right; bottom spans all three). `[hidden] { display: none !important; }`: a panel's own `display: grid` would otherwise beat the `hidden` attribute.
- App: a header with the three tab buttons (the active one has `aria-current="page"`). All three panels stay rendered and are toggled with `[hidden]`, so the open run's stream and a running evaluation survive a tab switch.
- `api(method, path, body?, headers?)`: `fetch`; a body is sent as JSON with `Content-Type: application/json`; a non-2xx answer throws `ApiError(status, detail)`, `detail` from the JSON body, else `statusText`. `detailText(detail)`: a string as is; FastAPI's list as `loc: msg` items joined by `; `.

**Event shapes the UI reads** (backend at `3800a53`). Every event: `{seq, run_id, t_ms, kind, node, tool, status, attention, msg, data, created_at}`.

| kind | node, tool, status | data |
|---|---|---|
| `stage` | node `guard` | `{steps, max_steps, tool_calls, max_tool_calls}` |
| `stage` | node `agent` (msg `LLM turn <n>`), `tools` (once per tools step), `finalize` | none |
| `stage` | node `kb.embed`, `kb.dense`, `kb.bm25`, `kb.rrf`; tool `search_knowledge_base`; status `ok`, `failed` or `skipped` | `{tool_call_id, ...}` |
| `llm` | node `agent`; status = outcome (`tool_calls`, `final`, `malformed`, `retry`, `unavailable`) | `{attempt, model, prompt_sha, prompt_tokens, completion_tokens, latency_ms, outcome, reason, tool_calls: [{id, name, args}]}` (`args` from T2) |
| `tool` | node `tools`; tool; status `ok` or the error type | `{tool_call_id, attempt, args, duration_ms, result}`: one per attempt, emitted after it; `attempt` 0 = refused; attention only on the last attempt |
| `retry` | node `tools` with a tool, or node `agent` (LLM) | `{tool_call_id?, attempt, delay_s, reason}` |
| `approval` | node `approval`; tool; status `pending` | `{tool_call_id, tool, args, interrupt_id, approval_id, expires_at, run_error}` |
| `approval` | status `approved`, `rejected`, `edited` or `expired` | `{approval_id, tool_call_id, decision: {decision, args?, reason?}, decided_by}` |
| `log` | none (audit) | `{actor, action, entity_id}` |
| `done` | status = final status | `{status, error, steps, tool_calls}` |
| `eval` | node `eval`; tool `search_knowledge_base` for search targets | `{target, metric, value, judge_model, error, threshold}`; after `done`, so only in `/trace` |
| `error` | status `failed` | none |

**Backend: calls in the `llm` event** (T2)
- `llm_gateway._llm_event`: each `tool_calls` item becomes `{id, name, args}` (`args` as parsed). The tracer masks secrets in it like in every event. This is M4's only backend change.

**TraceStore** (T2): `trace.ts`, a plain class with signals (no DI, no I/O). Everything is `computed` from the event list (`ponytail:` comment: recomputed per event, fine for hundreds of events).
- `add(event)` ignores an event whose `seq` is not above the last one: the `/trace` reads after `done` repeat old events.
- Running call: take the last `llm` event with `tool_calls`. Nothing runs until a `stage` event with node `tools` follows it. Then the running call is the first of its calls whose last `tool` event is missing or not final. A `tool` event is not final when its status is `timeout` or `unavailable` and it has no attention (a retry follows). Attempt = that event's `data.attempt` + 1 (1 when there is none). Args = the last `tool` event's args, else the args of an `edited` decision for the call, else the `llm` event's args.
- `timeline`, in event order:
  - one `llm` item per `llm` event;
  - right after it, one call item per call in its `tool_calls`: `{id, tool, args, attempts, durationMs, status, result, event, approval, badges}`. From the call's `tool` events, the run detail's `calls` rule: `attempts` = highest `data.attempt`, `durationMs` = sum, `result` from the last one. `args` as for the running call. `status`: `running` for the running call, else the last `tool` event's status, else the latest `approval` status for the call, else `waiting`. `approval` = the call's latest `approval` event; `event` = its latest `tool` or `approval` event (for the detail panel);
  - one `done` item for the `done` event;
  - `badges`: `eval` events with `data.target` `search:<call id>` go on that call item, `answer` on the `done` item.
- `now` = `{text, tool?, args?, attempt?}`, first rule that holds:
  1. a `done` event: its msg;
  2. the latest `approval` event is `pending`: its tool and args, text = its msg;
  3. a running call: its tool, args and attempt; text = the msg of the latest event about it (a retry's, for example), else `running`;
  4. the msg of the latest event that is not `log` or `eval`.
- `nodeOf(event)`: `stage` → `node`; `llm` → `agent`; `retry` → `tool`, else `node`; `tool` → `tool`; `approval` → `approval`; `done` → `finalize`; `log`, `eval`, `error` → none. Node ids are these event values; `flow.ts` (T4) uses the same ids.
- `nodes`: the latest event per node id. `activeNode`: none after `done`; the running call's tool (`tools` when there is none) when the latest event with a node is `stage tools`, a not-final `tool` event, any `tool` event while a call of the same reply still runs (the next call emits nothing until its first attempt ends; reviewer T2), or a tool `retry`; else that event's node. The running call counts only attempts since the latest `stage tools`, which a resume repeats. A call with no attempt when the run ended shows `not run`.
- `attention`: events with `attention`, newest first.
- `used` = `{steps, toolCalls, seconds}`. `steps` and `toolCalls` from the data of the latest `done` or `stage guard` event (0 before). `seconds` covers the current segment only (`max_run_seconds` is per segment): from the latest `log` event whose action is `create_run`, `decide_approval`, `expire_approval` or `resume_run` (else the first event) to the latest event that is not `eval`, on server `t_ms`. The limits come from the run detail (T3).
- `expectedEvals(hasFinal)`: search call items with status `ok` and at least one result, plus 2 when the run has a final answer (the plan in `eval/online.py`).
- `attentionStyle(event)` → `{colour, icon, word}` or null: `error` → red `✖`; `success` → green `✔`; `info` → blue `ℹ`; `warn` → amber `⚠` for kinds `approval` and `retry`, orange `⚠` for other kinds. `word` is the attention value (`warn`, `error`, `info`, `success`), the spec's words.
- `resultText(envelope)`: ok search → `<mode>, <n> results`; ok status → `<service> <status>`; ok incident → `<incident_id> <status>`; truncated → `truncated`; other ok → `ok`; error → `<type>: <message>`. Console lines and attention rows show the msg, plus `resultText` for an ok `tool` event, so a `sparse_only` search says so in words.

**Runs tab** (T3: left, center, detail panel; T4: right and bottom)
- Center header: short run id, status (from the run detail), steps used / max.
- Run form (`RunForm`, T3). Submit calls `preventDefault()`, reads `FormData`, builds the body with `runBody`, posts `/api/runs`, then emits `created(run_id)`; a 422 shows its detail. Fields:
  - objective: `<textarea required maxlength="2000">`;
  - LLM: `default` (no `llm` key), `fake`, `openai`;
  - evaluate: `default` (no key), `on`, `off`;
  - fault switches in `<details>`: one row per target, a mode `<select>` (`off` plus its modes) and `times` (min 1, blank = 1). `search_knowledge_base` and `get_service_status`: timeout, error, bad_output, latency. `create_incident`: the same plus timeout_after_commit. `llm`: malformed, timeout. `embeddings`: error. Latency keeps the server's default `ms`;
  - limits in `<details>`: one number input per key of `config.yaml > limits` (min 1, step 1, blank = the config value).
- `runBody(values)` is pure: only what was set; numbers as numbers (the API is strict: `"5"` is a 422); no `options` key when nothing was set.
- Runs list: `GET /api/runs` (20, newest first): short id, status word, objective (cut by CSS), local time. A click opens the run; a created run opens at once.
- Opening a run: close the previous stream, make a new `TraceStore`, then `followRun(id, onEvent)`.
- `followRun(runId, onEvent)` → `close()`: `new EventSource('/api/runs/<id>/events')`; each message's `data` is one event (no event names). After the `done` event it calls `close()`; otherwise the browser reconnects every few seconds and gets empty streams. No `onerror` handler: after a network error, or a stream that ended without `done` (an API restart, a cancel that waited over 5 s), the browser reconnects with `Last-Event-ID` by itself.
- `refresh()` reloads the runs list, the open run's `GET /api/runs/{id}` and (T5) `GET /api/approvals?status=pending`. It runs every 5 s (`REFRESH_MS`), when a run opens, after a create or a decision, and on each `approval` or `done` event of the open run (a cancel closes approvals without an `approval` event). Timers stop through `DestroyRef`.
- Evaluation badges. Online evaluation writes its `eval` events after `done`, and the stream closes at `done`. So after `done`, read the run detail; when its status is not `cancelled` and `options.evaluate` is true, read `GET /api/runs/{id}/trace` at once and then every 3 s, and add its events to the store. Stop when the store holds `expectedEvals(final != null)` `eval` events, after 40 reads (2 min), or when another run opens. `/trace`, not the run detail's `evals`: the events carry the attention and the threshold.
- Timeline (center, T3): `llm` items (msg, proposed calls as `name(args)`, tokens, latency); call items (tool, args as compact JSON, attempts, duration, `resultText`, approval status, badges); a `pending` approval with `run_error` `max_tool_calls` says that the run ends after this call; the `done` item shows the status, the error, the final answer (run detail) and the answer badges. A badge reads `<metric> <value>` (2 decimals), `n/a` for null with the error as `title`, styled by `attentionStyle`.
- Detail panel (bottom, T3): the selected event through the `json` pipe. A timeline item, a console line or an attention row selects its event.
- NOW bar (bottom, T4): from `now()`: `NOW <tool>(<args>) attempt <n>` and the text, or the text alone.
- Flow diagram (bottom, T4). `flow.ts` = `{nodes: [{id, label, x, y}], edges: [[from, to]]}`, positions placed by hand. Nodes: `guard`, `agent`, `approval`, `tools`, `search_knowledge_base`, `kb.embed`, `kb.dense`, `kb.bm25`, `kb.rrf`, `get_service_status`, `create_incident`, `finalize`. Edges: the spec's Agent loop (guard→agent, guard→finalize, agent→finalize, agent→guard, agent→approval, agent→tools, approval→tools, tools→guard), tools→each tool, search_knowledge_base→kb.embed→kb.dense→kb.rrf and search_knowledge_base→kb.bm25→kb.rrf. `FlowDiagram` draws only from this data: the active node is outlined; a node with an event gets a second line with the icon (when that event has attention) and the event's `status`; the colour class comes from `attentionStyle`. SVG attributes bind as `[attr.x]` and the like.
- Console (bottom, T4): one line per event: seconds since the run's first event, kind, node or tool, msg (plus `resultText`), icon and word when there is attention. Filters: All; Tools (events with a `tool`); Attention (events with attention).
- Budget meters (right, T4): a `<meter>` with a text label each for steps, tool calls and seconds: `used()` against the run detail's `options.limits` (`max_steps`, `max_tool_calls`, `max_run_seconds`).
- Attention list (right, T4): `attention()`: icon, word, msg (plus `resultText`), local time.

**Approval inbox** (T5)
- `ApprovalInbox` gets `approvals` from the runs page (`refresh()` loads `GET /api/approvals?status=pending`, oldest first). Outputs: `decided`, `openRun(run_id)`.
- Each item: tool, run (short id; a click emits `openRun`), args as pretty JSON, and a countdown `mm:ss` to `expires_at` from a 1 s timer on the browser clock. At zero it shows `expired` and keeps its buttons: the server accepts a decision until the sweep marks it (spec).
- Approve: `POST /api/runs/{run_id}/approvals/{id}` with `{"decision": "approve"}`. Reject: a reason input (`maxlength="500"`); send stays disabled until the reason is not blank; body `{decision: "reject", reason}`. Edit: a textarea prefilled with the args as pretty JSON; `JSON.parse` must give an object, else a local message and no request; body `{decision: "edit", args}`. One form open at a time; buttons disabled while a request runs.
- Header `X-Approver-Token` only when the token field is not empty. The field (`type="password"`, `autocomplete="off"`) sits in the inbox; the token lives in a signal only: never stored, never logged, asked again after a reload.
- Answers: 200 → emit `decided`. 404 or 409 → show the detail and emit `decided` (the list reloads). 401 or 422 → show the detail; the item stays (a 422 on edit keeps the approval pending).

**Evaluation and Incidents tabs** (T6)
- Evaluation, at start and after each run: `GET /api/eval/kb/latest`; a 404 shows its detail (`no evaluation report yet`).
- Run: `fetch('/api/eval/kb', {method: 'POST'})` with no body: every mode (`{}` would be a 422, since a body needs `modes`). Not 2xx → the `ApiError` detail (503 `knowledge base unavailable`). Else `readSse(response.body, onEvent)`: `progress` → `<progress>` and `done/total`; `report` → show it; `error` → its detail. The button is disabled while it runs.
- `readSse(body, onEvent)`: `TextDecoder` with `{stream: true}`; `\r\n` → `\n`; events end at a blank line, and the unfinished tail waits for the next chunk; `event:` (default `message`) and `data:` lines (joined by `\n`, then `JSON.parse`); `:` lines are comments (the `: ping` keep-alive).
- Report: created time, embed and judge models, and one row per mode from `summary`: questions, errors, hit@3, mrr@10, recall@3, context_precision, context_recall (2 decimals, `n/a` for null); `judge_error` under the table.
- Incidents: `GET /api/incidents` each time the tab is shown (`active` input): id, run (short id), title, severity, status, local time; the description in `title`.

**Not built** (not in the spec's UI section; add when someone asks)
- A health indicator (M3 handoff idea). If added later: each health call embeds one word, so poll every 30 s or more.
- Cancel and resume buttons (Postman and the CLI do both).
- A theme toggle (the OS setting decides), routes or deep links, a mode picker or per-question rows for the golden-set evaluation, the latency fault's `ms`, screenshots in the docs.

**Tests**
- Vitest through `ng test` (Node and jsdom). TestBed is zoneless: after changing inputs or signals, `await fixture.whenStable()`.
- `fetch` is stubbed with `vi.stubGlobal('fetch', vi.fn(...))` returning `new Response(...)`; `EventSource` with a small fake class (jsdom has none); timers with `vi.useFakeTimers()`.
- `trace.spec.ts` builds events with a helper `ev(kind, fields)` (increasing `seq` and `t_ms`), shaped like the table above.

**Tests changed on purpose**: none. The generated `app.spec.ts` is rewritten in T1 (new in this milestone). Checked: no backend test reads the `llm` event's `tool_calls` (`test_llm_gateway.py::test_llm_event_has_usage_and_prompt_sha` reads other keys), and no Postman test reads `llm` events.

## Library notes (checked 2026-09-28 on the npm registry, the angular-cli source at tag `v22.2.0`, the Angular changelog and docs, and FastAPI's installed `sse.py`)

**This machine.** The planner has no shell; found by file inspection: Homebrew Node 26.9.0 (`/opt/homebrew/Cellar/node/26.9.0`) with npm 11.19.1; `/opt/homebrew/bin/npm` and `npx` resolve. T1 confirms with `node --version` and `npm --version`.

**Angular 22.2.0** (npm `latest`; released 2026-09-23; `next` is 22.2.0-rc.0, no 23.x yet)
- Engines of `@angular/cli`, `core` and `build`: Node `^22.22.3 || ^24.15.0 || >=26.0.0`: Node 26.9 is supported.
- `ng new` 22.2 defaults: standalone, zoneless (no zone.js), Vitest with jsdom, routing on, file names `app.ts` (2025 style), `.prettierrc` (printWidth 100, single quotes). It writes `@angular/*` `^22.2.0`, `typescript ~6.0.2`, `vitest ^5.0.0` (npm `latest` 5.0.2), `jsdom ^30.0.0` (30.1.1), `prettier ^3.8.1`, `rxjs ~7.8.0`. Style and SSR prompt unless given.
- 22.0 breaking changes that matter here: a component without `changeDetection` is OnPush; TypeScript below 6.0 is refused. `@angular/compiler-cli` needs TypeScript `>=6.0 <6.1`, while npm's `latest` TypeScript is 7.0.2.
- `@angular/build` 22.2.0: Vite 8.3.0, esbuild 0.28.2. `ng test` (`@angular/build:unit-test`): runner `vitest`; without `browsers`, tests run in Node with jsdom; `watch` defaults to true in a terminal, so commands pass `--watch=false`; it picks up `**/*.spec.ts`.
- Dev server (`@angular/build:dev-server`) is Vite-based; `proxyConfig` sits in the `serve` options; the docs' key form is `"/api/**"`; restart `ng serve` after a proxy change.
- CLI analytics are off unless someone opts in; `NG_CLI_ANALYTICS=false` suppresses the prompt, `"cli": {"analytics": false}` keeps it off.
- Not used: Signal Forms and `httpResource`. One form submit and a few `fetch` calls are smaller.

**Vite proxy and SSE.** There are reports of buffered SSE through Vite's proxy (vitejs/vite discussion #10851; the backend header `X-Accel-Buffering: no` helps, and FastAPI sends it), and of a client close not reaching the server (issue #13522, fixed by PR #13578 in 2023). T3 checks it with curl (Proof).

**Browser APIs**
- `EventSource(url)`: GET only, no custom headers. It reconnects after a network error or an ended stream and sends `Last-Event-ID`; a non-200 answer or another content type closes it for good; `close()` stops it. Messages without an event name arrive in `onmessage`.
- Reading a `fetch` stream: `response.body.getReader()` and `TextDecoder` with `{stream: true}` (a character can be split across chunks).

**FastAPI 0.141.1 SSE wire format** (`fastapi/sse.py` in `backend/.venv`): lines end with `\n`; `event:`, then `data:` (JSON on one line), then `id:`; a blank line ends an event; keep-alive is `: ping`. `POST /api/eval/kb` with no body runs every mode (`test_api.py::test_eval_start_is_audited`).

## Risks
- SSE through the dev proxy may buffer. T3 checks it with curl. If events come in batches, stop and ask; the fallback is calling `http://localhost:8000` directly in development (`CORS_ORIGINS` already allows :4200).
- A stream left open after `done` reconnects every few seconds forever: `api.spec.ts` tests `followRun`.
- OnPush by default and zoneless: a plain field changed in a callback does not render. Rule: signals only.
- TypeScript: never `npm i typescript@latest` (7.x); Angular 22.2 needs 6.0. `npm ci` keeps the lock.
- Clocks: the countdown compares the browser clock with the server's `expires_at` (one machine in the demo); the time meter uses only server `t_ms`.
- A judge slower than 2 min leaves badges missing until the run is opened again.
- While the UI is open, uvicorn logs three access lines every 5 s.
- CI runs no frontend job until M5 T2: each task runs the frontend commands locally.
- M3 note, no UI change: `tracer.mask` masks values, not keys; the UI shows event data as the API sends it.

## Proof
Each task's tests pass at its own commit, with the backend and frontend commands in CLAUDE.md.

| AC | Tests | Task |
|----|-------|------|
| AC-13 (timeline live) | `trace.spec.ts` › timeline: call items follow their `llm` event with args; tool events grouped (highest attempt, summed duration, last result); approval status and edited args; eval badges by target | T2 |
| AC-13 (NOW bar) | `trace.spec.ts` › now: running call with tool, args and attempt through two retries (attempt 1, 2, 3); pending approval with args; edited args on the running call; `done` text | T2 |
| AC-13 (flow lights the running node) | `trace.spec.ts` › nodes: the active node is the running tool, then a `kb.*` sub-step, then none after `done`; latest event per node. `flow-diagram.spec.ts` › the active node is outlined; a node with attention shows its icon and status | T2, T4 |
| AC-13 (live stream) | `api.spec.ts` › `followRun` passes each event on and closes after `done`; an error does not close it | T3 |
| AC-13 (inbox) | `approval-inbox.spec.ts` › lists a pending approval with args and countdown; approve posts `{decision: approve}`; reject needs a reason; edit sends the parsed args, bad JSON sends nothing; 422 keeps the item and shows the detail; 409 shows the detail and emits `decided`; `X-Approver-Token` only when set | T5 |
| AC-13 (attention) | `trace.spec.ts` › `attentionStyle` for every row of the spec's attention table (colour, icon, word); a `sparse_only` search reads `sparse_only` in text | T2 |
| AC-13 (manual) | `docs/REVIEW_GUIDE.md` §4 "UI check (AC-13)", run by the owner at T5's gate 2 | T5 |

Other checks:
- T1: `app.spec.ts` › renders the three tabs; a click shows only that tab's panel.
- T2: `test_llm_gateway.py::test_llm_event_lists_calls_with_args` (two calls: `data.tool_calls` equals the parsed calls); `trace.spec.ts` › `add` ignores a repeated `seq`; `used` (steps and tool calls from guard and `done`; seconds of the second segment only); `expectedEvals`.
- T3: `run-form.spec.ts` › `runBody`: an objective alone gives `{objective}`; limits and `times` are numbers; `off` faults and `default` choices leave their keys out. Proxy check: with uvicorn on :8000 and `npm start`, `curl -s -X POST localhost:4200/api/runs -H 'content-type: application/json' -d '{"objective": "payments-api is returning 5xx errors. Investigate.", "options": {"faults": {"get_service_status": {"mode": "timeout", "times": 2}}, "evaluate": false}}'`, then `curl -N localhost:4200/api/runs/<id>/events` prints the two retries about 2 s apart as they happen and ends after `done`.
- T6: `api.spec.ts` › `readSse` joins an event split across two chunks, reads event names, skips `: ping`.

## Doc changes
Each note goes in the commit of the task that builds the behaviour.
- T1, `CLAUDE.md` Commands: add `cd frontend && npm ci` (install), `cd frontend && npm test -- --watch=false` (tests), `cd frontend && npm run build` (build and type check), `cd frontend && npx prettier --check src` (format; `--write` to fix). "Frontend and eval commands are added by the milestone that introduces them." becomes "Eval commands are added by the milestone that introduces them."
- T1, `README.md` Run: prerequisites gain "Node.js 22.22+, 24.15+ or 26+ (UI)"; new lines `cd frontend && npm ci && npm start` (UI on http://localhost:4200, with the API on :8000) and `cd frontend && npm test -- --watch=false`.
- T1, `docs/REVIEW_GUIDE.md` §1: `cd frontend && npm install && npm start` becomes `cd frontend && npm ci && npm start`.
- T2, `specs/ops-agent-harness.md`, LLM, after "... `completion_tokens` and `latency_ms`.": "A reply with tool calls also lists them in `tool_calls` as `{id, name, args}`, so the UI can show a call before it runs." `docs/DESIGN.md` §5: "token counts and latency" becomes "token counts, latency and the calls it proposed".
- T3, `docs/DESIGN.md` §4, row "Prompt injection in tool output": add "; the UI shows it as text, never as HTML".
- T5, `docs/DESIGN.md` §4, row "Side effect without consent": add "; the UI keeps the approver token in memory only". §8, new bullet: "**UI refresh.** The open run updates live. The runs list, the approval inbox and the open run's summary refresh every 5 s and on the open run's approval and done events, so an approval of another run can take 5 s to appear. The UI has no cancel or resume button; use the API or the CLI."
- T5, `docs/REVIEW_GUIDE.md` §4, after the table: "**UI check (AC-13).** With the API on :8000 and `npm start` in `frontend/`, open http://localhost:4200. 1. Start the Approve scenario: the timeline and the NOW bar show each call with its arguments and attempt, and the flow diagram lights the running node. 2. The approval appears in the inbox with a countdown. Approve it (or edit it, or reject it with a reason): the run continues, and the Incidents tab lists the incident. 3. Start the Retry, Step limit and Degraded search scenarios from the fault switches and limits: retries show amber, the limit red and `sparse_only` search blue, each with an icon and a word." Row R12 already names `trace.spec.ts` and `approval-inbox.spec.ts`.
- No ADR change: ADRs 0015 and 0016 already describe the layout, signals, the SVG drawn from a data file and the dev proxy. No Postman change: the API only gains `args` in `llm` event data, which no request checks. No new environment variable or config key.

## Handoffs
- M5 (ship):
  - Build: `cd frontend && npm ci && npm run build` writes `frontend/dist/frontend/browser/` (`index.html` and hashed files).
  - FastAPI serves it (M5 T1): mount `StaticFiles(directory=..., html=True)` at `/` after the API routers, only when the directory exists. The UI has no client routes, so it needs no `index.html` fallback, and it calls relative `/api` URLs, so production needs no CORS.
  - Docker: a Node build stage (22.22+, 24.15+ or 26+) runs the build; copy `dist/frontend/browser` into the image.
  - CI frontend job: `actions/setup-node` with the npm cache on `frontend/package-lock.json`, then `npm ci`, `npm test -- --watch=false`, `npm run build`, `npx prettier --check src`. The lock was made on macOS arm64 with npm 11; if `npm ci` on Linux misses an optional platform binary (a known npm issue), regenerate the lock rather than add packages by hand.
  - Review guide: the AC-13 row keeps `trace.spec.ts`, `approval-inbox.spec.ts` and the §4 UI check.

## Open questions
None blocking. Defaults taken from the spec and the ADRs, to confirm at gate 1:
1. T2 changes the backend by one line: the `llm` event lists calls with `args`. Without it the NOW bar shows a call's arguments only after its first attempt ends (2 s in the retry demo), which misses AC-13's "arguments". The alternative (no backend change) would re-read the run detail at each tools step.
2. Evaluation badges come from `/trace` after `done`, not from the run detail's `evals` (the M2 and M3 handoffs said the run detail): the events carry the attention and the threshold that colour a badge.
3. "Not built" leaves out the health indicator and cancel and resume buttons, which the spec's UI section does not list.
4. plans/README.md: M4 is now 5.5h (total 34h). Its "If time runs short" item 1 named "M4 T6, frontend specs", a task that no longer exists (each task carries its specs); it now names M4 T6, the Evaluation and Incidents tabs.

## Pipeline log
| Phase | Result | Notes |
|-------|--------|-------|
| Phase 1 planner | READY | Checked against the spec (UI, API notes, attention table, AC-13), ADRs 0014–0017, DESIGN, the M2 and M3 handoffs, the backend at `3800a53` (event shapes in the tracer, loop, LLM and tool gateways, runner, knowledge-base tool and online evaluation; the API routes; FastAPI's SSE wire format), Postman and the review guide. Kept 6 tasks, reshaped: scaffold; `TraceStore` with `args` in `llm` events; Runs tab left and center; right and bottom; approval inbox; Evaluation and Incidents tabs. Each task carries its specs. 7.5h → 5.5h; plans/README.md totals and the M4 cut updated. Pinned: the `ng new` command, signals-only state (OnPush default in Angular 22), `followRun` (close after `done`, no `onerror`), the store's rules (running call, NOW, nodes, attention, budget), refresh every 5 s and on approval and `done` events, badges from `/trace` after `done`, inbox rules (token in memory; 401, 404, 409, 422), the evaluation stream over `fetch` with no body. Library notes: Angular 22.2.0 (TypeScript 6.0, Vite 8.3, Vitest 5 with jsdom, zoneless); Node 26.9.0 and npm 11.19.1 on this machine. No open questions; four defaults to confirm at gate 1 |
| Gate 1 | approved | Owner approved on 2026-09-28 with the four defaults (backend `args` in `llm` events; badges from `/trace` after `done`; no health indicator, cancel or resume buttons, theme toggle, routes or evaluation mode picker; cut item 1 is T6). Owner allowed the npm downloads for T1 |
| T1 build | done | Skills: none in the Skills column. `node --version` v26.9.0, `npm --version` 11.19.1. `ng new` with the pinned command (it still adds `@angular/forms` and `@angular/router` to package.json; unused, left as generated); generated README.md and .vscode/ deleted; `npx prettier --write src`. `angular.json`: analytics off, `proxyConfig`. `proxy.conf.json`, `styles.css` (tokens, light and dark, the console grid, attention classes, `[hidden]` rule), `app.ts` (three tabs, panels kept rendered and toggled with `[hidden]`), `api.ts` (`api`, `ApiError`, `detailText`, types). `api.spec.ts` starts here (JSON body, `ApiError` detail, status-text fallback, validation-list text), since `api.ts` is new in T1. CLAUDE.md, README.md, REVIEW_GUIDE §1 commands. 5 frontend tests; build 111.81 kB initial |
| T1 tester | PASS | 5 frontend tests; build and prettier clean; backend 499 tests unchanged. Types in `api.ts` checked against the store schema, `run_detail`, the incidents fields and the golden report. Live proxy check: uvicorn on a scratch DB and `npm start`, `curl localhost:4200/api/health` answered through the proxy. No gap found |
| T1 reviewer | APPROVE | MINORs fixed: `api()` turns a network failure into `ApiError(0, ...)` and a non-JSON 2xx answer into `ApiError`; an empty status text falls back to `HTTP <status>`; `detailText` shows the msg alone for a `body`-only loc and never returns undefined. Specs added for each (8 frontend tests). Plan line 11 now says `api.spec.ts` starts in T1. NIT kept: the unused generated `@angular/forms` and `@angular/router` stay (tree-shaken; the rule limits additions, and the log records it) |
| T1 commit | 9d97ef4 | |
| T2 build | done | Skills: `ai-engineer` invoked. `trace.ts`: `TraceStore` (`add` ignores a repeated or older `seq`; `timeline`, `now`, `nodes`, `activeNode`, `attention`, `used`, `expectedEvals`; the running call as pinned, plus none after `done`) and `nodeOf`, `attentionStyle`, `resultText`, `eventText` (msg plus the result in words for an ok `tool` event; used by the console and attention rows in T4). `trace.spec.ts` (28 tests: every Proof row, every attention-table row). Backend: `llm` event `tool_calls` items are `{id, name, args}`; `test_llm_gateway.py::test_llm_event_lists_calls_with_args`. Spec LLM note, DESIGN §5. 36 frontend tests, backend 500 |
| T2 tester | PASS | 42 frontend tests. Added a real-run fixture (`trace.fixture.json`, 32 events from uvicorn on a scratch DB: search, two status timeouts, approval, incident, `done`) and 6 specs that replay it (NOW through attempts 1 to 3 with the real retry text, approval then running, active node, grouped timeline, `used`); `tsconfig.spec.json` gains `resolveJsonModule`. Backend 500 tests |
| T2 reviewer | REQUEST CHANGES, fixed | MAJOR fixed: with several calls in one reply, the finished call's tool stayed lit while the next call ran (the plan rule had the same gap; plan updated); specs for the next call, a refused call and a rejected call fail without the fix. MAJOR fixed: the fixture was not formatted and not listed; formatted and listed under Files. MINOR fixed: a call with no attempt when the run ended shows `not run`; the running call counts only attempts since the latest `stage tools` (a resume repeats the step). NIT fixed: an `Envelope` type in `api.ts` replaces `any` for results. NIT kept: the running call's text is the latest event about it, an approval msg included (plan rule). 46 frontend tests |
| T2 re-review | APPROVE | Every fix checked with its spec. NIT: the ok `Envelope` keeps `data: any`; narrow per tool if later tasks read more than `resultText`. 46 frontend tests, backend 500 |
| T2 commit | 5413b28 | |
| T3 build | done | Skills: none in the Skills column. `run-form.ts` (`RunForm`, `runBody`, `FAULT_TARGETS`, `LIMIT_KEYS`; native form read with `FormData`), `runs-page.ts`/`.html` (`RunsPage`: runs list, `open` with a new `TraceStore` and `followRun`, `refresh` every 5 s and on the open run's approval and done events, `/trace` reads every 3 s after `done` for badges, up to 40, timeline with results, approval status, the `max_tool_calls` note and badges, detail panel through the `json` pipe), `followRun` in `api.ts` (closed after `done`, no `onerror`), `app.ts` mounts the Runs tab (the app spec stubs `fetch`), styles for the form, list, timeline and badges. Specs: `run-form.spec.ts` (3), `api.spec.ts` followRun (2). DESIGN §4 row. Proxy check: uvicorn on a scratch DB and `npm start`; `curl -N localhost:4200/api/runs/<id>/events` on a live run with two status timeouts printed the two tool attempts 2.1 s apart as they happened and ended 4.7 s after the start, right after `done`. 51 frontend tests |
| T3 tester | PASS | 59 frontend tests. Added `runs-page.spec.ts` (6: a run renders events as they arrive; `done` closes the stream and refreshes; `/trace` reads after `done` stop at the expected evals; none when evaluate is off or the run is cancelled; another run closes the old stream and its reads; a failed runs list shows its error) and 2 `RunForm` specs (posts and emits `created`; a 422 shows its detail). Note for later specs: fake timers need `shouldAdvanceTime: true` with zoneless `whenStable()`. Backend 500 |
| T3 reviewer | APPROVE | MINORs fixed: `/trace` reads after `done` are keyed on the store, so reopening the same run during a read ends the old chain (spec fails with the old run-id check); `refresh()` fetches the list and the detail together and only the latest call writes, errors included (spec: an older answer arriving last does not win); badges show the attention word; an `interrupted` run gets a warn line under the header. NIT fixed: the open run row is also bold. NIT kept: a run whose row is final without `done` makes the browser reconnect every few seconds (plan accepts). Live check by the reviewer: the LM Studio judge took over 5 min for 3 metrics, past the 2 min badge window (plan risk). 61 frontend tests |
