#!/usr/bin/env bash
# Grade one scenario eval against a run's trace export.
#
#   evals/check.sh evals/<scenario>.json <trace.json>
#
# <trace.json> is the output of GET /api/runs/{id}/trace. The checks read the
# "expect" block of the scenario (final status, attempts per tool, incidents).
# Implemented in plans/m5-ship.md. Until then it fails on purpose: a grader
# that always passes is worse than none.
set -euo pipefail
eval_file="${1:?usage: check.sh <eval.json> <trace.json>}"
result_file="${2:?usage: check.sh <eval.json> <trace.json>}"
echo "Checking $(basename "$eval_file") against $(basename "$result_file")"
echo "evals/check.sh is not implemented yet (see plans/m5-ship.md)" >&2
exit 1
