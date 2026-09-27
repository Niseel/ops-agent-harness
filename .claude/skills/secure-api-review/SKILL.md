---
name: secure-api-review
description: Apply this project's API standard. Use whenever creating or modifying an endpoint under /api, reviewing API code, or updating the Postman collection.
---
# API standard

This version has no authentication or user accounts (a documented limitation in
docs/DESIGN.md). Every endpoint must still:

1. Validate input. Request bodies are Pydantic models that reject unknown fields
   (`extra="forbid"`); path and query parameters are typed. Invalid input returns
   422, never 500.
2. Never trust client limits. Limits sent by a client are clamped to config.yaml;
   `faults` are refused unless `ALLOW_FAULT_INJECTION=true`.
3. Record state changes. Creating a run, deciding an approval, resuming,
   cancelling and starting an evaluation each emit an event with actor
   (`current_user()`), action, entity id and timestamp.
4. Guard approvals. A decision is a conditional update: a second decision on the
   same approval returns 409. Edited arguments are validated with the tool's
   input model before anything runs.
5. Keep secrets out. API keys and tokens never appear in logs, events, errors or
   responses. Error bodies are `{"detail": ...}` without stack traces.
6. Treat tool output as data. It is returned or stored, never executed or used
   to build commands or queries.
7. Keep the contract in sync. Update the API table in specs/ops-agent-harness.md
   and docs/postman_collection.json in the same commit.
