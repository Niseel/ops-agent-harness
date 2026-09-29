#!/usr/bin/env bash
# Run scenario evals through the API and grade each one (spec: Evaluation, scenario evals).
#
#   evals/run.sh [evals/<scenario>.json ...]     # no argument: every evals/*.json
#
# Needs the API on BASE_URL (default http://localhost:8000) with fault injection allowed (the default), and
# curl and jq. APPROVER_TOKEN, when set, goes with each decision. Saves evals/out/<scenario>.json: the run's
# trace plus its incidents. Exit 1 when any scenario fails. Runs on bash 3.2 (macOS).
set -euo pipefail
cd "$(dirname "$0")/.."
BASE_URL="${BASE_URL:-http://localhost:8000}"
TIMEOUT_S=60   # a run not final by then is cancelled
FINAL=" completed failed limit_exceeded timed_out cancelled "

# request METHOD PATH [JSON]: sets $code and $body. The token goes only to decisions.
request() {
  local args=(-s -w '\n%{http_code}' -X "$1" "$BASE_URL$2")
  if [ -n "${3:-}" ]; then args+=(-H 'Content-Type: application/json' --data "$3"); fi
  case "$2" in */approvals/*) [ -z "${APPROVER_TOKEN:-}" ] || args+=(-H "X-Approver-Token: $APPROVER_TOKEN") ;; esac
  local out
  out=$(curl "${args[@]}") || out=$'curl failed\n000'
  code=${out##*$'\n'}
  body=${out%$'\n'*}
}

# run_scenario FILE: start the run, answer its approvals from `decisions`, save the result, grade it.
run_scenario() {
  local file=$1 stem run_id status decision approval_id decided=0 cancelled=0 start=$SECONDS
  stem=$(basename "$file" .json)
  request POST /api/runs "$(jq -c '{objective, llm, options: ({evaluate: false}
    + (if .limits then {limits} else {} end) + (if .faults then {faults} else {} end))}' "$file")"
  if [ "$code" != 202 ]; then
    echo "$stem:"
    echo "  FAIL create: HTTP $code $body"
    return 1
  fi
  run_id=$(jq -r .run_id <<<"$body")
  while :; do
    request GET "/api/runs/$run_id"
    status=$(jq -r .status <<<"$body" 2>/dev/null || true)   # not JSON (API down): no status, the timers decide
    case "$FINAL" in *" $status "*) break ;; esac
    if [ $cancelled = 0 ] && [ $((SECONDS - start)) -ge $TIMEOUT_S ]; then
      echo "  $stem: not final after ${TIMEOUT_S} s, cancelling"
      request POST "/api/runs/$run_id/cancel"
      cancelled=1
    elif [ $cancelled = 0 ] && [ "$status" = awaiting_approval ]; then
      decision=$(jq -c ".decisions[$decided] // empty" "$file")
      request GET "/api/approvals?status=pending"
      approval_id=$(jq -r --arg run "$run_id" '[.[] | select(.run_id == $run)][0].id // empty' <<<"$body")
      if [ -z "$decision" ]; then
        echo "  $stem: paused with no decision left, cancelling"
        request POST "/api/runs/$run_id/cancel"
        cancelled=1
      elif [ -n "$approval_id" ]; then
        request POST "/api/runs/$run_id/approvals/$approval_id" "$decision"
        if [ "$code" != 200 ]; then
          echo "  $stem: decision answered HTTP $code $body, cancelling"
          request POST "/api/runs/$run_id/cancel"
          cancelled=1
        fi
        decided=$((decided + 1))
      fi
    elif [ $((SECONDS - start)) -ge $((TIMEOUT_S + 10)) ]; then
      echo "$stem:"
      echo "  FAIL run $run_id is still $status after cancelling"
      return 1
    fi
    sleep 0.2
  done
  request GET "/api/runs/$run_id/trace"
  local trace=$body
  request GET /api/incidents
  # The incidents go through a file descriptor: as an argument the list would hit Linux's 128 KB limit.
  jq --arg run "$run_id" --slurpfile incidents <(printf '%s' "$body") \
    '. + {incidents: [$incidents[0][] | select(.run_id == $run)]}' <<<"$trace" >"evals/out/$stem.json"
  evals/check.sh "$file" "evals/out/$stem.json"
}

request GET /api/health
if [ "$code" != 200 ]; then
  echo "No API at $BASE_URL (HTTP $code). Start it first (README, Test)." >&2
  exit 1
fi
echo "API $BASE_URL: llm_default $(jq -r .llm_default <<<"$body"), knowledge base $(jq -r .kb.mode <<<"$body")"
[ $# -gt 0 ] || set -- evals/*.json
mkdir -p evals/out
passed=0
failed=0
for file in "$@"; do
  if run_scenario "$file"; then passed=$((passed + 1)); else failed=$((failed + 1)); fi
done
echo "$passed passed, $failed failed"
[ "$failed" = 0 ]
