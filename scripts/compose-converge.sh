#!/usr/bin/env bash
# compose-converge.sh — bounded replacement for compose-healer's whole-project
# `up -d` loop (which OOM-killed the stack mid-recreate twice on 2026-11-04).
# Only restarts services that are Exited/Created from a CORE allowlist —
# never recreates healthy containers, never pulls.
set -u
CORE="social-api social-api-gateway social-worker-default social-worker-publishing social-worker-media social-worker-messenger celery-beat social-postgres redis social-cloudflared social-frontend"
cd "$(dirname "$0")/.."

# Deploy lock — another agent/human is deploying; hands off entirely
# (same contract as compose-watchdog.sh). Fresh = DEPLOY_LOCK_MAX_AGE secs.
DEPLOY_LOCK="${DEPLOY_LOCK:-.deploy.lock}"
DEPLOY_LOCK_MAX_AGE="${DEPLOY_LOCK_MAX_AGE:-3600}"
if [ -f "$DEPLOY_LOCK" ]; then
  age=$(( $(date +%s) - $(date -r "$DEPLOY_LOCK" +%s) ))
  if [ "$age" -lt "$DEPLOY_LOCK_MAX_AGE" ]; then
    echo "converge: deploy lock fresh (${age}s) — skipping"
    exit 0
  fi
fi

# fsbridge probe — when \wsl.localhost bind mounts are poisoned, every
# docker start we issue just produces another exit-127. Probe ONE real
# source file; if it can't be read back through a mount, skip the pass.
if ! docker run --rm -v "$PWD/scripts/compose-converge.sh:/converge.probe:ro"     docker:27-cli sh -c 'test -s /converge.probe' >/dev/null 2>&1; then
  echo "converge: fsbridge mounts poisoned — skipping (heal via wsl --shutdown)"
  exit 0
fi

changed=0
restarted_svcs=""
for s in $CORE; do
  st=$(docker inspect -f '{{.State.Status}}' "$s" 2>/dev/null || echo missing)
  case "$st" in
    exited|created|dead)
      echo "converge: $s is $st -> docker start"
      docker start "$s" >/dev/null 2>&1 && { changed=$((changed+1)); restarted_svcs="$restarted_svcs $s"; } || echo "  start failed: $s"
      ;;
  esac
done
echo "converged: restarted $changed"

# --- flap detection ---------------------------------------------------------
# Two signatures, both alerted to the Telegram ops lane (send_telegram_alert
# dedups repeats for 6h anyway; the alerted_epoch fields are a second throttle):
#   a) converge restart-loop — same CORE service needed `docker start` >=3
#      times inside 2h (dies again between passes).
#   b) daemon crash-loop — .RestartCount grew >4/hour on any CORE service.
# State lives in /tmp (compose-healer is long-lived); a healer restart just
# re-baselines, worst case one duplicate alert.
FLAP=/tmp/compose-converge.flap      # svc epoch count alerted_epoch
FLAP_RC=/tmp/compose-converge.rc     # svc restartcount epoch alerted_epoch
FLAP_PER_HR=4
now=$(date +%s)

alert_ops() {  # $1 = text
  docker exec -i -e ALERT_TEXT="$1" social-api python - <<'PY' 2>/dev/null
import asyncio, os, sys
sys.path.insert(0, "/app")
from app.services.slack_notifications import send_telegram_alert
asyncio.run(send_telegram_alert("ops", "COMPOSE-FLAP: " + os.environ.get("ALERT_TEXT", "")))
PY
}

# (a) converge restart-loop
for s in $restarted_svcs; do
  epoch=$now; cnt=0; alerted=0
  if [ -f "$FLAP" ]; then
    set -- $(awk -v s="$s" '$1==s{print $2, $3, $4}' "$FLAP" 2>/dev/null)
    [ -n "${1:-}" ] && [ $((now - $1)) -lt 7200 ] && { epoch=$1; cnt=${2:-0}; alerted=${3:-0}; }
  fi
  cnt=$((cnt + 1))
  tmp=$(mktemp); touch "$FLAP"
  grep -v "^$s " "$FLAP" > "$tmp" 2>/dev/null
  echo "$s $epoch $cnt $([ "$cnt" -ge 3 ] && [ $((now - alerted)) -ge 21600 ] && echo $now || echo $alerted)" >> "$tmp"
  mv "$tmp" "$FLAP"
  if [ "$cnt" -ge 3 ] && [ $((now - alerted)) -ge 21600 ]; then
    echo "converge: $s restarted $cnt times in <2h — crash-looping"
    alert_ops "$s needs docker start every pass ($cnt x/2h) — container dies between heals. Check docker logs $s."
  fi
done

# (b) daemon restart-rate — .RestartCount delta per hour
if [ -n "$CORE" ]; then
  tmp=$(mktemp)
  for s in $CORE; do
    rc=$(docker inspect -f '{{.RestartCount}}' "$s" 2>/dev/null || echo 0)
    prc=0; pep=$now; palert=0
    set -- $(awk -v s="$s" '$1==s{print $2, $3, $4}' "$FLAP_RC" 2>/dev/null)
    [ -n "${1:-}" ] && { prc=$1; pep=${2:-$now}; palert=${3:-0}; }
    hrs=$(( (now - pep) / 3600 )); [ "$hrs" -lt 1 ] && hrs=1
    delta=$(( ${rc:-0} - prc ))
    newalert=$palert
    if [ "$delta" -gt $((FLAP_PER_HR * hrs)) ] && [ $((now - palert)) -ge 21600 ]; then
      echo "converge: $s RestartCount +$delta in ${hrs}h — daemon crash-loop"
      alert_ops "$s restarted $delta times in ${hrs}h (daemon) — crash-looping. Check docker logs $s."
      newalert=$now
    fi
    echo "$s ${rc:-0} $now $newalert" >> "$tmp"
  done
  mv "$tmp" "$FLAP_RC"
fi
