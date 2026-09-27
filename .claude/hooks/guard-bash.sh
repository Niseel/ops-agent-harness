#!/usr/bin/env bash
# PreToolUse (matcher: Bash). Blocks destructive commands for the main session
# and every subagent. Exit 2 = block; stderr is sent back to Claude.
INPUT="$(cat)"
CMD="$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')"
[ -z "$CMD" ] && exit 0
block(){ echo "Blocked by guard-bash.sh: $1. If truly needed, ask the user to run it manually." >&2; exit 2; }
S='[[:space:]]'
chk(){ printf '%s' "$CMD" | grep -Eiq -- "$1"; }
chk "rm${S}+-[a-z]*r[a-z]*${S}+(/|~|\\\$HOME|\.\.)" && block "recursive delete outside the project"
chk "git${S}+reset${S}+--hard"                        && block "git reset --hard discards work"
chk "git${S}+clean${S}+-[a-z]*f"                       && block "git clean deletes untracked files"
chk "git${S}+(checkout|restore)${S}+(--${S}+)?\.(${S}|$)" && block "discarding all local changes"
chk "git${S}+branch${S}+-D"                            && block "force-deleting a branch"
chk "git${S}+push"                                     && block "pushing is done by a human"
chk "(drop|truncate)${S}+(table|database|schema)"      && block "destructive SQL"
chk "(curl|wget)[^|]*\|${S}*(sudo${S}+)?(ba|z)?sh"     && block "piping a download into a shell"
chk "(^|[;&|]${S}*)sudo${S}"                           && block "sudo"
chk "chmod${S}+-R${S}+777"                             && block "chmod -R 777"
exit 0
