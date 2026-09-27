#!/usr/bin/env bash
# PreToolUse (matcher: Bash). Production deploys require a named authorization.
# Exit 2 = block; the message goes to Claude.
CMD="$(jq -r '.tool_input.command // empty' < /dev/stdin)"
if printf '%s' "$CMD" | grep -Eiq 'deploy' && printf '%s' "$CMD" | grep -Eiq 'prod'; then
  if [ -z "${RELEASE_APPROVAL:-}" ]; then
    echo "Production deploy needs a release authorization (set RELEASE_APPROVAL)." >&2
    exit 2
  fi
fi
exit 0
