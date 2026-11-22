#!/bin/bash
# pr_state_watch — session-level PR transition watcher.
# Polls `gh pr list` every INTERVAL seconds, prints a line on every
# state/check/failure transition, exits when no PRs remain open or after
# MAX_ITERS polls. The durable watch lives in cron (pr-land-watch,
# pr-ci-fix-watch); this is for live in-session reporting.
#
# Usage: pr_state_watch.sh [interval_s] [max_iters]
cd "$(git rev-parse --show-toplevel 2>/dev/null || echo ~/cu130-slim)" || exit 1
INTERVAL="${1:-120}"; MAX="${2:-60}"
declare -A last
for i in $(seq 1 "$MAX"); do
  out=$(gh pr list --json number,state,mergeStateStatus,statusCheckRollup \
    -q '.[]|[.number,.state,.mergeStateStatus,([.statusCheckRollup[]|select(.conclusion=="FAILURE" or .conclusion=="ACTION_REQUIRED" or .conclusion=="TIMED_OUT")|.name//.context]|join("|"))]|@tsv' 2>/dev/null)
  if [ -z "$out" ]; then echo "$(date +%H:%M) no open PRs — done"; break; fi
  while IFS=$'\t' read -r n s m f; do
    k="$n:$s:$m:$f"
    if [ "${last[$n]:-}" != "$k" ]; then
      echo "$(date +%H:%M) PR $n $s/$m ${f:+FAILS:$f}"
      last[$n]="$k"
    fi
  done <<< "$out"
  sleep "$INTERVAL"
done
echo "$(date +%H:%M) watch ended after $MAX polls"
