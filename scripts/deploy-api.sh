#!/usr/bin/env bash
# Zero-downtime deploy for social-api (blue/green behind api-gateway).
#
# Flow:  detect live slot → start the other slot (runs `alembic upgrade head`
#        on boot — keep migrations expand-only) → wait for /health → rewrite
#        gateway upstream → nginx -s reload → verify through gateway → the old
#        slot keeps running as instant rollback until you stop it.
#
# Usage:
#   scripts/deploy-api.sh            # deploy current working tree to idle slot
#   scripts/deploy-api.sh --stop-old # also stop the old slot after cutover
set -euo pipefail
cd "$(dirname "$0")/.."

ACTIVE_CONF=deploy/nginx/upstream/active.conf
GATEWAY=social-api-gateway
STOP_OLD=0
[[ "${1:-}" == "--stop-old" ]] && STOP_OLD=1

active_slot() { grep -oE 'social-api(-green)?' "$ACTIVE_CONF" | head -1; }
slot_port()   { [[ "$1" == "social-api" ]] && echo 8083 || echo 18083; }

wait_healthy() { # $1=service $2=port
  for i in $(seq 1 60); do
    if curl -sf "http://localhost:$2/health" >/dev/null 2>&1; then return 0; fi
    sleep 3
  done
  return 1
}

LIVE=$(active_slot)
if [[ "$LIVE" == "social-api" ]]; then
  NEW=social-api-green; COMPOSE=(docker compose --profile bluegreen)
else
  NEW=social-api; COMPOSE=(docker compose)
fi

echo "==> live slot: $LIVE — deploying to $NEW"

echo "==> starting $NEW (bind-mount picks up current working tree; alembic runs on boot)"
"${COMPOSE[@]}" up -d "$NEW"

echo "==> waiting for $NEW /health on :$(slot_port "$NEW")"
if ! wait_healthy "$NEW" "$(slot_port "$NEW")"; then
  echo "!! $NEW never became healthy — aborting, traffic still on $LIVE" >&2
  exit 1
fi

echo "==> switching gateway upstream to $NEW"
printf 'server %s:8000 max_fails=2 fail_timeout=10s;\n' "$NEW" > "$ACTIVE_CONF"
docker exec "$GATEWAY" nginx -t >/dev/null
docker exec "$GATEWAY" nginx -s reload
sleep 1

echo "==> verifying traffic through gateway"
curl -sf "http://localhost:$(slot_port "$NEW")/health" >/dev/null
if docker exec "$GATEWAY" wget -qO- "http://127.0.0.1:8080/health" >/dev/null 2>&1; then
  echo "==> gateway serving from $NEW — cutover complete"
else
  echo "!! gateway check failed — rolling back to $LIVE" >&2
  printf 'server %s:8000 max_fails=2 fail_timeout=10s;\n' "$LIVE" > "$ACTIVE_CONF"
  docker exec "$GATEWAY" nginx -s reload
  exit 1
fi

if [[ $STOP_OLD -eq 1 ]]; then
  echo "==> stopping old slot $LIVE (90s drain)"
  docker stop -t 90 "$LIVE" >/dev/null
  echo "==> $LIVE stopped"
else
  echo "==> $LIVE left running as instant rollback: scripts/rollback-api.sh"
fi
