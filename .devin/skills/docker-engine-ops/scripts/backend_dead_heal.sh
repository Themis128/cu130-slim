#!/usr/bin/env bash
# backend_dead_heal.sh — event-triggered heal for the Docker Desktop
# backend-down crash class (seen 2026-11-28): dd-user-distro-proxy
# restart-loops on
#   "dial unix /mnt/wsl/docker-desktop/shared-sockets/host-services/backend.sock:
#    connect: no such file or directory"
# meaning the Windows-side Desktop backend is dead — bare `docker info`
# fails, engine-guard fires proxy-dead, worker tasks die on DNS/socket
# errors. The only remediation is `docker desktop restart` (the nightly
# 03:20 cron covers the slow-degradation case; this covers the hard drop).
#
# Runs every 10 min from cron. Exits quietly when: docker works, the
# backend is already starting, or a restart happened inside the cooldown.
# Logs transitions only to ~/logs/backend-dead-heal.log; TG #ops on action.

set -u
OPS_HOME="${OPS_HOME:-/home/tbaltzakis}"
LOG="$OPS_HOME/logs/backend-dead-heal.log"
STATE="$OPS_HOME/.local/state/backend-dead-heal.state"
STAMP="$OPS_HOME/.local/state/backend-dead-heal.stamp"
COOLDOWN_S="${COOLDOWN_S:-2700}"          # min gap between desktop restarts
MAX_RESTARTS="${MAX_RESTARTS:-2}"         # per incident before escalating
SOCK_DIR="/mnt/wsl/docker-desktop/shared-sockets/host-services"
DOCKER_EXE="/mnt/c/Users/tbaltzakis/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe"
TG="$OPS_HOME/cu130-slim/.devin/skills/telegram-alert-ops/scripts/tg_alert.py"
mkdir -p "$(dirname "$LOG")" "$(dirname "$STATE")"

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >>"$LOG"; }
alert() { python3 "$TG" ops "$1" >/dev/null 2>&1 || true; }

# Fast path: engine answering through the proxy — nothing to do.
if [ "$(timeout 15 docker info --format '{{.Name}}' 2>/dev/null)" = "docker-desktop" ]; then
  [ -f "$STATE" ] && { log "backend recovered"; rm -f "$STATE" "$STAMP"; }
  exit 0
fi

# Confine to the backend-dead signature so we don't restart for unrelated
# flaps (vsock coin-toss, socket theft — those have their own lanes).
backend_sock_missing=0
[ -S "$SOCK_DIR/backend.sock" ] || backend_sock_missing=1
journal_hits=$(journalctl -u dd-user-distro-proxy --since '-15m' --no-pager 2>/dev/null \
  | grep -c 'backend.sock: connect: no such file' || true)
if [ "$backend_sock_missing" = 0 ] && [ "${journal_hits:-0}" -lt 2 ]; then
  exit 0   # proxy dead for another reason — guard alerts, doctor fixes
fi

source "$OPS_HOME/scripts/win-hidden.sh"
dd_status() { winrun "$DOCKER_EXE" desktop status 2>/dev/null | awk '/^Status/{print $2}'; }

# A restart is already in flight (watchdog, nightly cron, or us) — wait.
if [ "$(dd_status)" = "starting" ]; then
  [ "$(cat "$STATE" 2>/dev/null)" != "starting" ] && { log "desktop already starting — waiting"; echo starting >"$STATE"; }
  exit 0
fi

# Cooldown + per-incident restart budget.
now=$(date +%s)
restarts=0; last=0
[ -f "$STAMP" ] && read -r last restarts <"$STAMP" || true
if [ $((now - last)) -lt "$COOLDOWN_S" ]; then exit 0; fi
if [ "$restarts" -ge "$MAX_RESTARTS" ]; then
  if [ "$(cat "$STATE" 2>/dev/null)" != "escalated" ]; then
    log "backend still dead after $restarts restarts — escalating"
    alert "backend-dead-heal: Docker Desktop backend still dead after ${restarts} restarts — needs manual fix (wsl --shutdown or Desktop reinstall)"
    echo escalated >"$STATE"
  fi
  exit 1
fi

log "backend dead (sock_missing=$backend_sock_missing journal_hits=$journal_hits) — docker desktop restart"
alert "backend-dead-heal: Desktop backend down (proxy crash-loop on backend.sock) — restarting Docker Desktop"
echo restarting >"$STATE"
winrun "$DOCKER_EXE" desktop restart >>"$LOG" 2>&1
echo "$now $((restarts + 1))" >"$STAMP"
