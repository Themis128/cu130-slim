#!/usr/bin/env bash
# Metrics port-proxy watchdog.
#
# Docker Desktop restarts sometimes break the Windows port-proxy for
# 192.168.1.23:9390 -> social-metrics:80: TCP accepts, HTTP resets, the
# container stays "healthy", and Prometheus reports up{job="socialauto"}=0.
# A container restart re-registers the forward. This script probes the
# published endpoint and restarts social-metrics only when the proxy path
# is dead — never resurrects a deliberately stopped container.
#
# Scheduled via Windows Task Scheduler:
#   wsl.exe -d Ubuntu-26.04 -e bash /home/tbaltzakis/cu130-slim/scripts/metrics-proxy-watchdog.sh
set -u

URL="http://localhost:9390/metrics"
CONTAINER="social-metrics"
LOG="${HOME}/.cache/metrics-proxy-watchdog.log"
mkdir -p "$(dirname "$LOG")"

log() { echo "$(date -u +%FT%TZ) $*" >> "$LOG"; }

# healthy path — nothing to do
if curl -s -m 8 -o /dev/null "$URL"; then
  exit 0
fi

# only act if the container exists and is running (don't resurrect a stopped stack)
state=$(docker inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null || echo "missing")
if [ "$state" != "running" ]; then
  log "probe failed but $CONTAINER is '$state' — leaving alone"
  exit 0
fi

log "probe failed while $CONTAINER running — port-proxy dead, restarting"
docker restart "$CONTAINER" >> "$LOG" 2>&1
sleep 8
if curl -s -m 10 -o /dev/null "$URL"; then
  log "recovered: $URL answering after restart"
else
  log "STILL BROKEN after restart — needs manual check"
fi

# keep the log small
tail -n 200 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
