#!/usr/bin/env bash
# Grade one scenario eval against its saved result (spec: Evaluation, scenario evals).
#
#   evals/check.sh evals/<scenario>.json evals/out/<scenario>.json
#
# The result is what evals/run.sh saves: GET /api/runs/{id}/trace plus `incidents`, the run's rows from
# GET /api/incidents. One `ok` or `FAIL` line per check; exit 1 when any check fails. Needs jq.
set -euo pipefail
scenario="${1:?usage: check.sh <scenario.json> <result.json>}"
result="${2:?usage: check.sh <scenario.json> <result.json>}"

# Each check prints "<ok|FAIL><TAB><what><TAB><expected and got>". Each one can fail when its fault or
# decision never fired: a grader that always passes is worse than none.
program='
def line(ok; what; detail): (if ok then "ok" else "FAIL" end) + "\t" + what + "\t" + detail;
$s[0] as $scenario | $r[0] as $result | ($result.events // []) as $events | $scenario.expect as $expect
# 1. Exactly one `done` event, with the expected final status.
| ([$events[] | select(.kind == "done") | .status]) as $done
| line($done == [$expect.status]; "status"; "expected one done with \($expect.status), got \($done)"),
# 2. The decisions recorded in `approval` events, in order, match the listed ones.
( ([$events[] | select(.kind == "approval" and (.status | IN("approved", "rejected", "edited", "expired")))]
    | sort_by(.seq) | map(.status)) as $got
  | ([$scenario.decisions[].decision | {approve: "approved", reject: "rejected", edit: "edited"}[.]]) as $want
  | line($got == $want; "decisions"; "expected \($want), got \($got)") ),
# 3. Attempts per tool (its `tool` events with attempt >= 1; a refused or rejected call has only attempt 0),
#    or `llm` (its `llm` events).
( ($expect.attempts // {}) | to_entries[] | .key as $name | .value as $want
  | (if $name == "llm" then [$events[] | select(.kind == "llm")]
     else [$events[] | select(.kind == "tool" and .tool == $name and (.data.attempt // 0) >= 1)] end | length) as $got
  | line($got == $want; "attempts \($name)"; "expected \($want), got \($got)") ),
# 4. Incidents in the incident system for this run.
( if ($expect | has("incidents")) | not then empty
  elif ($result | has("incidents")) | not then line(false; "incidents"; "the result has no incidents list")
  else ($result.incidents | length) as $got | line($got == $expect.incidents; "incidents"; "expected \($expect.incidents), got \($got)")
  end ),
# 5. The mode of every search result.
( if ($expect | has("search_mode")) | not then empty
  else [$events[] | select(.kind == "tool" and .tool == "search_knowledge_base" and .status == "ok")
          | .data.result.data.mode] as $modes
  | line(($modes | length) > 0 and all($modes[]; . == $expect.search_mode); "search_mode";
         "expected \($expect.search_mode) in every search, got \($modes)")
  end )
'

echo "$(jq -r .name "$scenario"):"
if ! jq -e . "$result" >/dev/null 2>&1; then
  echo "  FAIL result: $result is not JSON"
  exit 1
fi
lines=$(jq -r -n --slurpfile s "$scenario" --slurpfile r "$result" "$program")
failed=0
while IFS=$'\t' read -r verdict what detail; do
  printf '  %-4s %s: %s\n' "$verdict" "$what" "$detail"
  [ "$verdict" = ok ] || failed=1
done <<<"$lines"
exit "$failed"
