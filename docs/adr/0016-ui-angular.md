# 0016. UI tech: Angular

Status: Accepted · Date: 2026-09-26

## Context

The UI ([0015](0015-ui-run-console-not-chat.md)) needs a live flow diagram, a timeline, a console, a detail panel, and light and dark themes. All of them update from the same live event stream (Server-Sent Events).

## Options

| Option | Pros | Cons |
|---|---|---|
| Angular | Signals fit "many panels derived from one event stream". Standalone components, strict typing, test runner built in. | Heavier toolchain. Reviewers need Node for dev mode. |
| One vanilla HTML/JS file | No build step. | Hard to structure and test once there are many panels. |
| React + Vite | Popular. Large ecosystem. | Needs extra choices (state library, test setup) for the same result. |

## Decision

Angular (standalone components, signals, plain CSS with design tokens, light/dark theme).

- One `TraceStore` turns events into node states, timeline items and console lines. Every panel reads from it.
- The flow diagram is plain SVG drawn from a small data file (nodes and edges), so changing the graph does not touch rendering code.
- In development, `ng serve` proxies `/api` to FastAPI. In the Docker image, FastAPI serves the built UI on the same port.

## Consequences

- Panels stay simple: they render signals, they do not parse events.
- Docker lets reviewers run the UI without installing Node.
