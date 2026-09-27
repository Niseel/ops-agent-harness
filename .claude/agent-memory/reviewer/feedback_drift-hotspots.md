---
name: spec-adr-config-drift-hotspots
description: Places where spec, ADRs, config.yaml and the plan have drifted before; check them first on any doc or harness review
metadata:
  type: feedback
---

Drift found in the first spec review (2026-09-27), worth re-checking on later changes:

- Golden set path had three values: spec `evals/`, ADR 0008 `data/eval/`, config.yaml + config.py default `eval/`. ADR 0018 moved it to `evals/` but did not annotate 0008.
- `max_repairs` boundary: ADRs 0010/0011 and config comment say "fail after N consecutive malformed", the owner's plan says "fail when exceeding N". ACs must pin the exact boundary.
- Approval lifecycle vs run cancel/expiry: pending approvals of a cancelled run must not be decidable or swept into a resume (could create an incident after cancel).
- ADR 0015 has five attention colours, the event schema has four `attention` values.
- Audit events for non-run actions (start evaluation) have no `run_id` home in `events`.
- FakePlanner behaviour is written in ADR 0003's decision text; the spec narrowed it (propose an incident only when the objective asks) in round 2, so ADR 0003 needs a status-line note.
- FakePlanner follows injected instructions found in search results (spec, ADR 0003 note). If the injected KB doc ranks in the top 3 for other scenario objectives, those runs pause for approval and break scenario evals. Check the M2 fixtures and M5 scenarios for this.
- The `embeddings` fault key (ADR 0012 note) has no attempt counter in the spec and is easy to miss in M2 (tools/kb.py).
- Resolution pattern the owner accepts: clarifications to an accepted ADR go in a status-line note plus a note on its row in docs/adr/README.md (as done for 0008, 0010, 0011, 0017). When a spec change alters behaviour an ADR describes, check that the ADR got such a note.

**Why:** accepted ADRs cannot be rewritten (docs/adr/README.md), so drift piles up unless each follow-up ADR or status note is checked.
**How to apply:** when a change touches any of these areas, compare all sources with file:line; a fix to an accepted ADR is a status-line note or a new ADR, never an edit of the decision.
