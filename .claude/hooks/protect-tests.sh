#!/usr/bin/env bash
# PreToolUse (matcher: Edit|Write). Guards the feedback loop: an agent fixing a
# bug must not weaken the test that proves it. Set FIX_MODE=1 in the environment
# during a fix task to activate. Exit 2 = block.
[ "${FIX_MODE:-0}" = "1" ] || exit 0
INPUT="$(cat)"
FILE="$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty')"
[ -z "$FILE" ] && exit 0
case "$FILE" in
  *test*|*spec*|*/tests/*|*/__tests__/*)
    echo "Blocked by protect-tests.sh: cannot edit test files during a fix task. Fix the code instead." >&2
    exit 2 ;;
esac
exit 0
