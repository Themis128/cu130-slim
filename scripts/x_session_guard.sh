#!/usr/bin/env bash
# Ensure the bridge's X session is logged in before deferred X batches fire.
# Cheap status probe; only runs seed-x when X is walled. docker.exe per
# docker-engine-ops (survives WSL docker.sock flaps).
set -u
DOCKER=docker.exe
API=social-api
SRC=/home/tbaltzakis/cu130-slim/.devin/skills/bridge-session-ops/scripts/bridge_sessions.py

$DOCKER cp "$SRC" "$API":/tmp/bridge_sessions.py >/dev/null 2>&1 || exit 0
OUT=$($DOCKER exec -w /app "$API" python /tmp/bridge_sessions.py status 2>&1) || exit 0
XLINE=$(printf '%s\n' "$OUT" | grep -i "^x\b\|^x " | head -1)
if printf '%s' "$XLINE" | grep -qi "logged_in"; then
  echo "x session ok: $XLINE"
else
  echo "x walled ($XLINE) — seeding"
  $DOCKER exec -w /app "$API" python /tmp/bridge_sessions.py seed-x 2>&1 | tail -3
fi
