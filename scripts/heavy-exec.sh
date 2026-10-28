#!/usr/bin/env bash
# heavy-exec — run ad-hoc python where it CAN'T OOM-kill the production API.
#
# social-api is mem_limited to 1GiB (steady-state ~600MiB): heavy scripts
# (remember()/embeddings, schedule_report_actions, pytest, media gen) get
# SIGKILLed at 137 there. Workers get 1.25GiB and are idle-tolerant.
#
# Usage:
#   scripts/heavy-exec.sh                          # python3 from stdin, in social-worker-default
#   scripts/heavy-exec.sh file.py                  # run a repo file (bind-mounted at /app/scripts/... won't exist;
#                                                  # pass stdin instead, or use --container for bind-mounted paths)
#   scripts/heavy-exec.sh --container social-worker-publishing file.py
#   scripts/heavy-exec.sh --api ...                # explicitly run in social-api (light work only)
#
# Exit 137 (OOM) → one automatic retry in the next worker in the rotation.
set -u
CONTAINER="social-worker-default"
TARGET=""
ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --container) CONTAINER="$2"; shift 2;;
    --api) CONTAINER="social-api"; shift;;
    *) ARGS+=("$1"); shift;;
  esac
done
if [ ${#ARGS[@]} -gt 0 ]; then TARGET="${ARGS[0]}"; fi

ROTATION="social-worker-default social-worker-publishing social-worker-media social-worker-messenger"

run_in() {
  local c="$1"; shift
  MSYS_NO_PATHCONV=1 docker exec -i "$c" python3 "$@"
}

next_in_rotation() {
  local prev=""
  for c in $ROTATION; do
    [ "$prev" = "$1" ] && { echo "$c"; return; }
    prev="$c"
  done
  echo "social-worker-default"
}

if [ -n "$TARGET" ]; then
  echo "[heavy-exec] $CONTAINER python3 $TARGET" >&2
  run_in "$CONTAINER" "$TARGET" < /dev/null
else
  echo "[heavy-exec] $CONTAINER python3 <stdin>" >&2
  run_in "$CONTAINER"
fi
rc=$?

if [ "$rc" -eq 137 ]; then
  RETRY=$(next_in_rotation "$CONTAINER")
  echo "[heavy-exec] OOM-killed (137) in $CONTAINER — retrying in $RETRY" >&2
  if [ -n "$TARGET" ]; then
    run_in "$RETRY" "$TARGET" < /dev/null
  else
    echo "[heavy-exec] cannot replay stdin automatically — re-run the command" >&2
    exit 137
  fi
  rc=$?
fi
exit "$rc"
