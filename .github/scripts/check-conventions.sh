#!/usr/bin/env bash
# Checks branch name, pull request title and commit messages against CONTRIBUTING.md.
# CI sets BRANCH, TITLE, BASE and HEAD. Locally:
#   BRANCH=$(git branch --show-current) BASE=main HEAD=HEAD .github/scripts/check-conventions.sh
set -euo pipefail

types='feat|fix|docs|test|refactor|perf|build|ci|chore|revert'
header="^($types)(\([a-z0-9-]+\))?!?: [^[:space:]].*$"
fail=0
err() { echo "::error::$1"; fail=1; }

check_header() {  # $1 = label, $2 = text
  if ! grep -Eq "$header" <<<"$2" || [ ${#2} -gt 100 ]; then
    err "$1 '$2' must be '<type>(<scope>): <summary>', at most 100 characters"
  fi
}

if [ -n "${BRANCH:-}" ] && ! grep -Eq "^($types)/[a-z0-9][a-z0-9._-]*$" <<<"$BRANCH"; then
  err "branch '$BRANCH' must look like <type>/<slug>, e.g. feat/m1-harness-core"
fi

[ -n "${TITLE:-}" ] && check_header "PR title" "$TITLE"

for sha in $(git rev-list --no-merges "${BASE:?}..${HEAD:?}"); do
  msg=$(git log -1 --format=%B "$sha")
  check_header "commit ${sha:0:7}" "$(head -1 <<<"$msg")"
  if grep -Eiq '^co-authored-by:|generated with' <<<"$msg"; then
    err "commit ${sha:0:7}: remove Co-Authored-By / 'Generated with' lines"
  fi
done

exit "$fail"
