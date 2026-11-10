#!/usr/bin/env bash
# newsletter-nurture-daily — paced LinkedIn newsletter nurture batch.
#
# Issue #794 approved "paced LinkedIn newsletter nurture batches". This runs
# ONE bounded batch per day (--limit below) through the LinkedIn browser
# sidecar inside social-worker-default (1.25GiB — social-api's 1GiB OOMs the
# script; see .devin/skills/owner-gate-ops).
#
# Safety:
#   - sidecar warm-up preflight: waits for /health browser_connected=true so
#     the first touches don't race the idle-sleep wake (root cause of the
#     earlier batch failures, confirmed in #794).
#   - hard cap per run via --limit; script itself enforces MAX_PER_RUN=20 and
#     jittered pauses.
#   - every touch flips Lead New -> In Process in EspoCRM, so re-runs are
#     idempotent — a missed/aborted day just continues the next day.
#   - exits non-zero + logs on sidecar-down or run failure; cron keeps going.
#
# Cron (user crontab): 30 8 * * *  /home/tbaltzakis/cu130-slim/scripts/newsletter-nurture-daily.sh
set -u

REPO="/home/tbaltzakis/cu130-slim"
LOG="/home/tbaltzakis/logs/newsletter-nurture.log"
LIMIT="${NEWSLETTER_NURTURE_LIMIT:-10}"
WORKER="${NEWSLETTER_NURTURE_WORKER:-social-worker-default}"

mkdir -p "$(dirname "$LOG")"
log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" | tee -a "$LOG"; }

# --- sidecar warm-up preflight (max ~3 min) ---
for i in $(seq 1 18); do
  health="$(docker exec "$WORKER" python3 -c "
import urllib.request, json
try:
    print(urllib.request.urlopen('http://linkedin-browser-sidecar:9225/health', timeout=8).read().decode())
except Exception as e:
    print('{}')
" 2>/dev/null)"
  conn="$(printf '%s' "$health" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('browser_connected') and d.get('has_session') and not d.get('rate_limited'))" 2>/dev/null)"
  if [ "$conn" = "True" ]; then
    log "preflight ok (attempt $i)"
    break
  fi
  [ "$i" = "18" ] && { log "ABORT: sidecar not ready after 3min — skipping today"; exit 1; }
  sleep 10
done

log "run --approve --limit $LIMIT on $WORKER"
docker exec "$WORKER" python3 /app/scripts/newsletter_nurture.py run --approve --limit "$LIMIT" >>"$LOG" 2>&1
rc=$?
if [ $rc -eq 0 ]; then
  log "done rc=0"
else
  log "FAILED rc=$rc — see log above"
fi
exit $rc
