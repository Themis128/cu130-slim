#!/usr/bin/env bash
# Instant rollback: point api-gateway back at the previous API slot.
# Works while the previous slot container still exists (running or stopped —
# stopped slots are restarted first).
set -euo pipefail
cd "$(dirname "$0")/.."

ACTIVE_CONF=deploy/nginx/upstream/active.conf
GATEWAY=social-api-gateway

active_slot() { grep -oE 'social-api(-green)?' "$ACTIVE_CONF" | head -1; }
slot_port()   { [[ "$1" == "social-api" ]] && echo 8083 || echo 18083; }

CUR=$(active_slot)
if [[ "$CUR" == "social-api" ]]; then
  PREV=social-api-green; COMPOSE=(docker compose --profile bluegreen)
else
  PREV=social-api; COMPOSE=(docker compose)
fi

echo "==> live: $CUR — rolling back to $PREV"

# Ensure the previous slot is up and healthy before switching.
if ! docker inspect -f '{{.State.Running}}' "$PREV" 2>/dev/null | grep -q true; then
  echo "==> $PREV not running — starting it"
  "${COMPOSE[@]}" up -d "$PREV"
fi
for i in $(seq 1 40); do
  curl -sf "http://localhost:$(slot_port "$PREV")/health" >/dev/null 2>&1 && break
  sleep 3
done
curl -sf "http://localhost:$(slot_port "$PREV")/health" >/dev/null || { echo "!! $PREV unhealthy — abort" >&2; exit 1; }

printf 'server %s:8000 max_fails=2 fail_timeout=10s;\n' "$PREV" > "$ACTIVE_CONF"
docker exec "$GATEWAY" nginx -t >/dev/null
docker exec "$GATEWAY" nginx -s reload
sleep 1
docker exec "$GATEWAY" wget -qO- http://127.0.0.1:8080/health >/dev/null \
  && echo "==> rollback complete — traffic on $PREV" \
  || { echo "!! gateway verify failed" >&2; exit 1; }
