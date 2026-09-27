---
name: private-name-leak-check
description: The owner's plan file names an earlier private project that must never appear in the repo; check every doc review for it without writing the name here
metadata:
  type: project
---

The approved project plan (outside the repo, under ~/.claude/plans/) names an earlier private project that code and UI were reused from. That name must not appear anywhere in the repo.

**Why:** owner's rule; this memory dir is version controlled, so the name itself is deliberately not written here.

**How to apply:** on every review, read the plan's "Context" section (the reuse-source line) to learn the name, then grep the changed files for it case-insensitively (also hyphen/no-hyphen variants). Flag any hit as a BLOCKER.
