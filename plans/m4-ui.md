# Plan: M4 run console UI   (from [specs/ops-agent-harness.md](../specs/ops-agent-harness.md))

Status: draft (gate 1 before the milestone starts). Branch: `feat/m4-ui`. PR title: `feat: M4 run console UI`.

The web UI from [ADR 0015](../docs/adr/0015-ui-run-console-not-chat.md) and [ADR 0016](../docs/adr/0016-ui-angular.md): start runs, watch them live, decide approvals, see what needs attention, compare search modes.

## Files that change
- `frontend/` (new) - Angular app: `package.json`, `angular.json`, `proxy.conf.json` (`/api` → port 8000)
- `frontend/src/styles/` (new) - design tokens (light/dark), base, components
- `frontend/src/app/core/` (new) - API client with SSE (`Last-Event-ID`), `TraceStore` (events → node states, timeline, NOW bar, attention), types, theme
- `frontend/src/app/flow/` (new) - graph data (one node per tool, knowledge-base sub-steps, loop edge), SVG renderer, console, detail panel
- `frontend/src/app/runs/` (new) - run form with fault switches and limits, runs list, run timeline
- `frontend/src/app/side/` (new) - approval inbox, budget meters, attention list
- `frontend/src/app/eval/`, `frontend/src/app/incidents/` (new) - evaluation tab, incidents tab
- `CLAUDE.md` (edit, T1) - frontend commands

## Order of work
1. T1 → T2 → T3 → T4 → T5 → T6.

## Tasks
| ID | Task | Files | Skills | Depends on | Parallel | Est. |
|----|------|-------|--------|------------|----------|------|
| T1 | Scaffold: Angular app, styles and theme, API client with SSE, `TraceStore`, flow renderer, console, detail panel | frontend/src/{styles,app/core,app/flow}, CLAUDE.md | - | M3 | no | 1.5h |
| T2 | Run form (objective, LLM mode, fault switches, limits, evaluate), runs list, run timeline (AC-13) | frontend/src/app/runs | - | T1 | no | 2h |
| T3 | Approval inbox (TTL countdown, approve, edit, reject with reason), budget meters, attention list (AC-13) | frontend/src/app/side | - | T2 | no | 1h |
| T4 | Tool-aware flow graph, NOW bar, console filters, attention colours with icon and text (AC-13) | frontend/src/app/flow, core/trace.ts | - | T3 | no | 1.5h |
| T5 | Evaluation tab (modes compared), RAGAS badges (read from `GET /api/runs/{id}` after `done`, not the live stream), incidents tab (AC-13) | frontend/src/app/eval, incidents | ai-engineer | T4 | no | 1h |
| T6 | Frontend specs: `TraceStore` (node states, NOW bar, attention mapping), approval inbox (AC-13) | frontend/src/**/*.spec.ts | - | T5 | no | 0.5h |

## Risks
- `EventSource` cannot send headers; reconnection uses its own `Last-Event-ID`. The server must set `id:` on every event (spec: API).
- Colour alone must never carry meaning; each attention level also has an icon and a word.
- The UI and the API run on different ports in development; the dev proxy avoids CORS problems.

## Proof
| AC | Evidence |
|----|----------|
| AC-13 | `trace.spec.ts` (event → node state, NOW bar text, attention colour per kind), `approval-inbox.spec.ts` (decide calls the API, 409 shown); manual steps in `docs/REVIEW_GUIDE.md` with screenshots |

## Pipeline log
| Phase | Result | Notes |
|-------|--------|-------|
