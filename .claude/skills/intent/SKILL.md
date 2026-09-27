---
name: intent
description: Capture an idea as a version-controlled intent.md (Stage 1 Plan). Use when someone has an idea, ticket, or incident to turn into an actionable artifact.
argument-hint: "[free text idea]"
disable-model-invocation: true
---
# Capture intent

Idea: $ARGUMENTS

1. Brainstorm with the user until the idea is concrete. Ask what an analyst would:
   scope, users, constraints, what success looks like, what is out of scope.
2. Write the result to intent/<slug>.md using intent/_TEMPLATE.md.
3. Let the user correct anything you misunderstood.
4. Summarize and tell them the next step is /spec intent/<slug>.md.

Do not start design or code here. This stage only produces intent.md.
