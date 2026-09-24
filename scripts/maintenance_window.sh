#!/usr/bin/env bash
# maintenance_window.sh — hold the shared browser bridge for manual ops.
#
# The browser-novnc bridge (:9223) is shared by pollers (celery-beat tasks)
# and every platform that needs browser automation. Manual CM/profile edits
# get hijacked by rotating pollers — this tool pauses the poller fleet so a
# manual session stays stable, then restores it.
#
# Usage:
#   scripts/maintenance_window.sh start [minutes]   # pause pollers (default hold note only)
#   scripts/maintenance_window.sh stop              # unpause everything
#   scripts/maintenance_window.sh status            # show paused containers + bridge owner
#
# What it pauses: celery-beat (stops new scheduled tasks) plus the workers
# that run browser-touching tasks (publishing, default, messenger, media).
# social-api stays up so API/UI keep working.

set -euo pipefail
cd "$(dirname "$0")/.."

WORKERS=(celery-beat social-worker-publishing social-worker-default social-worker-messenger social-worker-media)

cmd="${1:-status}"

case "$cmd" in
  start)
    echo "Pausing poller fleet: ${WORKERS[*]}"
    docker compose pause "${WORKERS[@]}"
    echo ""
    echo "Poller fleet paused. The bridge may still hold a busy-owner briefly —"
    echo "check 'status' and use force:true on /session/start if it won't release."
    echo "Run '$0 stop' when finished."
    ;;
  stop)
    echo "Resuming poller fleet"
    docker compose unpause "${WORKERS[@]}"
    echo "Done — verify with: docker compose ps"
    ;;
  status)
    echo "── paused containers ──"
    docker ps --filter "status=paused" --format "{{.Names}}" | sort || true
    echo "── bridge session ──"
    curl -s http://localhost:9223/session/status 2>/dev/null || echo "bridge unreachable"
    echo ""
    ;;
  *)
    echo "usage: $0 {start|stop|status}" >&2
    exit 2
    ;;
esac
