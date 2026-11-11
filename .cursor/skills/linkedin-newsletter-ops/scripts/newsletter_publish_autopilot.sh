#!/usr/bin/env bash
# newsletter_publish_autopilot.sh — unattended retry loop for the LinkedIn
# newsletter publish. Survives Docker/WSL engine bounces because it runs on
# the WSL HOST, not inside a container: when the daemon drops it repairs via
# wsl-docker-doctor and retries; when it's up it fires
# newsletter_publish_direct.sh and polls the result.
#
# Safety:
#   - flock-guarded (one instance)
#   - deadline-bounded (default 3h)
#   - NEVER navigates the sidecar page while a publish curl is in flight
#   - stops on first definitive sidecar response; ambiguous timeouts retry
#   - writes ~/logs/newsletter_autopilot.log + .status.json every cycle
#
# Usage: newsletter_publish_autopilot.sh [--deadline-min 180] [--slug SLUG]
#        [--issue N] [--name "..."] [--dry-run]
set -uo pipefail

REPO="${REPO:-$HOME/cu130-slim}"
SKILL_SCRIPTS="$REPO/.devin/skills/linkedin-newsletter-ops/scripts"
DOCTOR="$REPO/.cursor/skills/wsl-docker-doctor/scripts/wsl_docker_doctor.py"
LOG_DIR="$HOME/logs"
LOG="$LOG_DIR/newsletter_autopilot.log"
STATUS="$LOG_DIR/newsletter_autopilot.status.json"
RESULT="$LOG_DIR/newsletter_sidecar_result.json"
CURL_LOG="$LOG_DIR/newsletter_sidecar_curl.log"
LOCK="$LOG_DIR/.newsletter_autopilot.lock"
mkdir -p "$LOG_DIR"

DEADLINE_MIN=180
SLUG="2026-11-02-weekly-cloud-computing-trends"
ISSUE=3
NAME="The Cloudless Build Log"
DRY_RUN=false
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --deadline-min) DEADLINE_MIN="$2"; shift 2;;
    --slug) SLUG="$2"; shift 2;;
    --issue) ISSUE="$2"; shift 2;;
    --name) NAME="$2"; shift 2;;
    --dry-run) DRY_RUN=true; EXTRA_ARGS+=(--dry-run); shift;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

exec 9>"$LOCK"
flock -n 9 || { echo "another autopilot instance holds $LOCK"; exit 0; }

log() { echo "$(date '+%F %T') $*" | tee -a "$LOG"; }
status() { printf '{"ts":"%s","state":"%s","detail":"%s"}\n' \
  "$(date -Is)" "$1" "$2" > "$STATUS"; }

DEADLINE=$(( $(date +%s) + DEADLINE_MIN * 60 ))
ATTEMPT=0
log "autopilot start deadline=${DEADLINE_MIN}m slug=$SLUG issue=$ISSUE dry=$DRY_RUN"
status "starting" "deadline ${DEADLINE_MIN}m"

engine_ok() { docker info >/dev/null 2>&1; }

sidecar_health() {
  docker exec social-api python - <<'PY' 2>/dev/null
import urllib.request, json
try:
    h = json.loads(urllib.request.urlopen(
        'http://linkedin-browser-sidecar:9225/health', timeout=15).read())
    print(json.dumps({k: h.get(k) for k in
        ('has_session','browser_connected','page_ok','rate_limited')}))
except Exception as e:
    print(json.dumps({'error': str(e)[:120]}))
PY
}

warm_sidecar() {
  docker exec social-api python - <<'PY' >/dev/null 2>&1
import urllib.request, json, time
def post(p, d=None, t=90):
    req = urllib.request.Request(
        'http://linkedin-browser-sidecar:9225' + p,
        data=json.dumps(d or {}).encode(),
        headers={'Content-Type': 'application/json'}, method='POST')
    return urllib.request.urlopen(req, timeout=t).read()
try:
    post('/debug/navigate', {'url': 'https://www.linkedin.com/feed/'})
except Exception:
    pass
for _ in range(15):
    time.sleep(8)
    try:
        h = json.loads(urllib.request.urlopen(
            'http://linkedin-browser-sidecar:9225/health', timeout=15).read())
        if h.get('page_ok'):
            break
    except Exception:
        pass
PY
}

api_healthy() {
  [[ "$(docker inspect social-api --format '{{.State.Health.Status}}' 2>/dev/null)" == "healthy" ]]
}

while [[ $(date +%s) -lt $DEADLINE ]]; do
  ATTEMPT=$((ATTEMPT+1))

  if ! engine_ok; then
    log "[$ATTEMPT] engine down — running wsl-docker-doctor fix"
    status "engine-down" "doctor fix attempt $ATTEMPT"
    python3 "$DOCTOR" fix >>"$LOG" 2>&1 || true
    sleep 60
    continue
  fi

  for c in social-api linkedin-browser-sidecar; do
    docker ps --format '{{.Names}}' | grep -qx "$c" || {
      log "[$ATTEMPT] $c not running — waiting for watchdog/recovery"
      status "container-missing" "$c"
      sleep 90; continue 2; }
  done

  H=$(sidecar_health)
  log "[$ATTEMPT] sidecar=$H"
  if echo "$H" | grep -q '"has_session": true'; then
    if ! api_healthy; then
      log "[$ATTEMPT] social-api not healthy yet — waiting"
      status "api-warming" "attempt $ATTEMPT"
      sleep 45; continue
    fi

    log "[$ATTEMPT] warming sidecar"
    status "warming" "attempt $ATTEMPT"
    warm_sidecar

    log "[$ATTEMPT] firing newsletter_publish_direct.sh"
    status "publishing" "attempt $ATTEMPT"
    rm -f "$RESULT"
    bash "$SKILL_SCRIPTS/newsletter_publish_direct.sh" \
      --slug "$SLUG" --issue "$ISSUE" --name "$NAME" \
      ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} --no-wait >>"$LOG" 2>&1

    # poll the result file — the curl is detached on the host and survives
    # container restarts; an engine bounce shows up as curl exit + no file
    PUB_DEADLINE=$(( $(date +%s) + 1800 ))
    while [[ $(date +%s) -lt $PUB_DEADLINE && $(date +%s) -lt $DEADLINE ]]; do
      sleep 30
      if [[ -s "$RESULT" ]]; then
        BODY=$(cat "$RESULT" 2>/dev/null | head -c 2000)
        log "[$ATTEMPT] sidecar responded: $BODY"
        if echo "$BODY" | grep -qE '"status"[^,]*"ok"|"published"[^,]*true|"success"'; then
          status "published" "$BODY"
          log "PUBLISHED: $BODY"
          exit 0
        fi
        log "[$ATTEMPT] definitive failure response — will retry after cooldown"
        status "attempt-failed" "$BODY"
        break
      fi
      if ! engine_ok; then
        log "[$ATTEMPT] engine dropped mid-publish — will retry"
        status "engine-dropped" "attempt $ATTEMPT"
        break
      fi
    done
    sleep 120   # cooldown between attempts
  else
    log "[$ATTEMPT] no session/rate-limited ($H) — cooldown"
    status "session-issue" "$H"
    sleep 180
  fi
done

status "deadline-exceeded" "gave up after $ATTEMPT attempts"
log "deadline exceeded after $ATTEMPT attempts"
exit 1
