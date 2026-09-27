# Intent: agent harness for an operations assistant
Author: Thanh Nguyen (AI Engineer candidate). Status: accepted.

## Problem
Operators want to hand an objective to an LLM agent, for example "payments-api is returning 5xx, investigate and open an incident if needed". A bare LLM loop is not safe to run and is hard to inspect:

- tools fail, hang or return bad data,
- the LLM sometimes returns malformed replies,
- the loop can run forever,
- side effects (opening an incident) can happen without a human decision,
- nobody can see afterwards what ran and why.

## Proposed outcome
A harness around the LLM that:

- runs the LLM–tool loop with validated tool inputs and outputs,
- keeps the run's state and history, and survives a restart,
- retries transient failures, repairs malformed replies and stops at limits,
- asks a human before `create_incident` and waits for the decision,
- traces every step, and shows it live in a UI.

## Affected users and systems
- Requesters: operators who start runs from the API, the CLI or the UI.
- Approvers: on-call people who approve, edit or reject incidents.
- Reviewers of this assessment: they run it, read the trace and check the tests.
- Mock systems: knowledge base, service status, incident system.

## Constraints
- 2–3 days of work.
- Must run for a reviewer with no API key and no model server (fake LLM by default).
- Approval before `create_incident` is mandatory; no automatic approval.
- No real external systems; all three tools are mocks.
- No authentication in this version.
- Everything in the repo is in English.

## Open questions
None. Decisions are recorded in [docs/adr/](../docs/adr/README.md). Next step: [specs/ops-agent-harness.md](../specs/ops-agent-harness.md).
