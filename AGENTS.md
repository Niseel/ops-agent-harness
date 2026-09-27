<!-- ai-sdlc:start -->
## AI-Native SDLC

Instructions for any coding agent (Claude, Codex, Cursor, Copilot, Gemini CLI...).
The loop and its artifacts are described in docs/AI-SDLC.md.

### Artifacts
- intent/ -> why. specs/ -> what. plans/ -> how. One file per change.
- REVIEW.md -> review policy. evals/ -> end-to-end scenario checks.

### Commands that prove a change works
See "Commands" in CLAUDE.md.

### Do not
- Do not push, merge, deploy, or rewrite git history. A human does that.
- Do not weaken, skip or delete a test to make a suite pass. Fix the code.
- Do not edit .claude/hooks/** or .claude/settings.json.
- Do not commit secrets.

### What done means
1. The commands pass, and the output is pasted into the reply as evidence.
2. Every acceptance criterion in the plan is met, or listed as not met.
<!-- ai-sdlc:end -->

Project rules (branches, commit messages, language) are in CLAUDE.md and CONTRIBUTING.md.
