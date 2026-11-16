#!/usr/bin/env bash
# stack-ops-bridge.sh — lifecycle for the interim fsbridge-outage proxy.
#
# Context: 2026-11-16 flap #6 — poisoned hashed bind mounts killed stack-ops
# (Exited 127). `stack-ops-bridge` is a no-bind-mount python:3.12-slim
# container that answers the `stack-ops` DNS alias on cu130-backend and
# TCP-relays every services.json proxy port to its real backend, plus the
# same host ports on 127.0.0.1. It has NO wake/sleep logic and no :8787
# control API — remove it as soon as real stack-ops is healthy again.
#
# fsbridge-reconcile.sh retires it automatically on heal; this script is the
# manual/recreate path. Idempotent.
#
#   stack-ops-bridge.sh up      — create/start bridge (safe to re-run)
#   stack-ops-bridge.sh down    — remove bridge (ONLY when real stack-ops is up)
#   stack-ops-bridge.sh status  — show bridge + real stack-ops state
set -euo pipefail

REPO="${SOB_REPO:-/home/tbaltzakis/cu130-slim}"
RELAY="$REPO/stack-ops/bridge-relay.py"
NAME="stack-ops-bridge"
IMAGE="python:3.12-slim"
NET="cu130-backend"

# host port -> container port (mirrors the published set; 8199 is in-network
# only on real stack-ops but was host-published on the bridge for probes)
PORTS="9223 9224 9225 9226 9227 9228 9230 8000 8010 8080 8888 5555 9000 3000 5900 6080 6081 1080 8199"

port_args() {
    local args=()
    for p in $PORTS; do args+=(-p "127.0.0.1:${p}:${p}"); done
    printf '%s\n' "${args[@]}"
}

cmd_up() {
    [ -f "$RELAY" ] || { echo "missing relay script: $RELAY" >&2; exit 1; }
    if docker inspect "$NAME" >/dev/null 2>&1; then
        st=$(docker inspect -f '{{.State.Status}}' "$NAME")
        [ "$st" = "running" ] && { echo "$NAME already running"; return 0; }
        docker rm -f "$NAME" >/dev/null
    fi
    # shellcheck disable=SC2046
    docker run -d --name "$NAME" --network "$NET" --network-alias stack-ops \
        --restart unless-stopped $(port_args | tr '\n' ' ') \
        "$IMAGE" sleep infinity >/dev/null
    docker cp "$RELAY" "$NAME":/tmp/relay.py
    docker exec -d "$NAME" sh -c 'python /tmp/relay.py > /tmp/relay.log 2>&1'
    sleep 2
    docker exec "$NAME" sh -c 'head -3 /tmp/relay.log' || true
    echo "$NAME up — alias stack-ops on $NET, ${#PORTS} routes"
}

cmd_down() {
    # Guard: never remove while real stack-ops is absent/dead.
    real=$(docker ps -a --filter "name=^stack-ops$" --format '{{.Status}}' | head -1)
    if ! docker inspect stack-ops >/dev/null 2>&1; then
        echo "REFUSING: no container named stack-ops exists" >&2; exit 1
    fi
    st=$(docker inspect -f '{{.State.Status}}' stack-ops 2>/dev/null || echo missing)
    if [ "$st" != "running" ]; then
        echo "REFUSING: real stack-ops is '$st' — bridge still needed" >&2; exit 1
    fi
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    echo "$NAME removed — real stack-ops owns the alias"
}

cmd_status() {
    echo "== bridge =="
    docker inspect "$NAME" --format '{{.State.Status}} started={{.State.StartedAt}}' 2>/dev/null || echo "absent"
    docker exec "$NAME" sh -c 'tail -5 /tmp/relay.log 2>/dev/null' 2>/dev/null || true
    echo "== real stack-ops =="
    docker inspect stack-ops --format '{{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' 2>/dev/null || echo "absent"
}

case "${1:-status}" in
    up) cmd_up ;;
    down) cmd_down ;;
    status) cmd_status ;;
    *) echo "usage: $0 {up|down|status}" >&2; exit 2 ;;
esac
