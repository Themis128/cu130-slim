#!/usr/bin/env bash
# SocialAuto stack watchdog — catches the failure modes that need a
# human today. Host-level (outside compose) so it sees stack failures.
# Every 10 min via the Windows scheduled task (the ONLY schedule — do not
# also add a WSL cron line, two schedulers race each other):
#   conhost.exe --headless "C:\Program Files\Git\bin\bash.exe" -lc '~/cu130-slim/scripts/compose-watchdog.sh'
#
# Safe-autonomy rules (see AGENTS.md "Deploy lock & auto-healers"):
#   * This script is the SOLE owner of container/stack restarts. stack-watchers
#     and autoheal never restart the whole stack.
#   * Deploy lock: while $DEPLOY_LOCK exists and is fresh, take NO action.
#   * Max $WD_MAX_RESTARTS restarts per container per hour, with backoff
#     (1, 5, 15 min) between attempts; Slack alert when the cap is hit.
#   * Crash loops caused by migrations (alembic / "Can't locate revision")
#     are never restarted: restart policy set to "no" + one alert.
#   * `docker desktop restart` at most $WD_DD_MAX_PER_DAY times per day.
#   * State persists in $WD_STATE_DIR (not /tmp); log in logs/watchdog.log.
set -u
cd "$(dirname "$0")/.." || exit 1
REPO_DIR="$(pwd)"

WD_STATE_DIR="${WD_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/compose-watchdog}"
WD_LOG="${WD_LOG:-$REPO_DIR/logs/watchdog.log}"
DEPLOY_LOCK="${DEPLOY_LOCK:-$REPO_DIR/.deploy.lock}"
DEPLOY_LOCK_MAX_AGE="${DEPLOY_LOCK_MAX_AGE:-3600}"   # seconds; older = stale
WD_MAX_RESTARTS="${WD_MAX_RESTARTS:-3}"              # per container per hour
WD_BACKOFF="${WD_BACKOFF:-60 300 900}"                # seconds after restart 1,2,3
WD_DD_MAX_PER_DAY="${WD_DD_MAX_PER_DAY:-2}"
WD_NOW="${WD_NOW:-}"                                  # test hook: fixed epoch
# raw-webhook fallback for ALERT when the API is down
if [ -z "${SLACK_ALERTS_WEBHOOK_URL:-}" ] && [ -f .env ]; then
  SLACK_ALERTS_WEBHOOK_URL=$(grep -oP '^SLACK_ALERTS_WEBHOOK_URL=\K.*' .env 2>/dev/null | tr -d '"' | head -1)
fi
mkdir -p "$WD_STATE_DIR" "$(dirname "$WD_LOG")"
exec >>"$WD_LOG" 2>&1

now() { if [ -n "$WD_NOW" ]; then echo "$WD_NOW"; else date +%s; fi; }
log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [watchdog] $*"; }
mtime() { stat -c %Y "$1" 2>/dev/null || echo 0; }

# ── deploy lock: an agent/human is deploying — hands off ──────────────
if [ -f "$DEPLOY_LOCK" ]; then
  age=$(( $(now) - $(mtime "$DEPLOY_LOCK") ))
  if [ "$age" -lt "$DEPLOY_LOCK_MAX_AGE" ]; then
    log "deploy lock present (${age}s old, owner: $(head -c 200 "$DEPLOY_LOCK" | tr '\n' ' ')) — skipping all actions"
    exit 0
  fi
  log "deploy lock is STALE (${age}s > ${DEPLOY_LOCK_MAX_AGE}s) — ignoring it"
  STALE_LOCK=1
fi

# 0. Docker Desktop backend dead? (npipe gone / engine unresponsive —
#    every docker call would fail below anyway). `docker desktop restart`
#    is the documented fix: ~20s, containers return on restart policies.
#    Capped at $WD_DD_MAX_PER_DAY per day and one per 30 min.
if ! docker info >/dev/null 2>&1; then
  log "docker engine unreachable"
  DD_FILE="$WD_STATE_DIR/dd-restarts"
  today=$(date -u -d "@$(now)" +%F)
  n_today=$(grep -c "^$today " "$DD_FILE" 2>/dev/null || true)
  last=$(tail -1 "$DD_FILE" 2>/dev/null | awk '{print $2}')
  if [ "${n_today:-0}" -ge "$WD_DD_MAX_PER_DAY" ]; then
    log "docker desktop restart cap reached ($n_today/$WD_DD_MAX_PER_DAY today) — needs manual attention"
  elif [ -n "$last" ] && [ $(( $(now) - last )) -lt 1800 ]; then
    log "docker desktop restarted <30min ago — waiting"
  else
    echo "$today $(now)" >> "$DD_FILE"
    DD_CLI="/c/Program Files/Docker/Docker/resources/bin/docker.exe"
    [ -x "$DD_CLI" ] || DD_CLI="docker"
    if "$DD_CLI" desktop restart >/dev/null 2>&1; then
      log "docker desktop restart issued ($((n_today+1))/$WD_DD_MAX_PER_DAY today) — engine returns in ~20s"
    else
      log "docker desktop restart failed — needs manual attention"
    fi
  fi
  exit 0
fi

ALERT() {  # post to the ops Slack channel, throttled to once/30min per key
  local key="$1" msg="$2"
  local f
  f="$WD_STATE_DIR/alert-$(echo "$key" | md5sum | cut -c1-8)"
  log "ALERT[$key] $msg"
  [ -f "$f" ] && [ $(( $(date +%s) - $(stat -c %Y "$f") )) -lt 1800 ] && return
  docker compose exec -T social-api python -c "
import asyncio
from app.services.slack_notifications import post_alert_to_slack
asyncio.run(post_alert_to_slack('$msg'))" >/dev/null 2>&1 && touch "$f" || {
  # api down — fall back to the raw webhook so the outage still alerts
  [ -n "${SLACK_ALERTS_WEBHOOK_URL:-}" ] && curl -sf -X POST "$SLACK_ALERTS_WEBHOOK_URL" -H 'Content-Type: application/json' -d "{\"text\":\"$msg\"}" >/dev/null 2>&1 && touch "$f" || true
}
}
CORE="social-api social-worker-default social-worker-publishing social-worker-media social-worker-messenger celery-beat redis social-postgres"
[ "${STALE_LOCK:-0}" = 1 ] && ALERT "stale-lock" ":warning: stale deploy lock $DEPLOY_LOCK — watchdog resumed; remove it if the deploy is finished"

# Migration / schema crash? Restarting can never fix it and a loop hides
# the real error — disable the restart policy and alert exactly once.
MIGRATION_RE="${WD_MIGRATION_RE:-Can.t locate revision|alembic[^ ]*(\.util)?.*(Error|exception|FAILED)|CommandError|(migration|migrate).*(error|failed)|Multiple head revisions|DuplicateTable|UndefinedTable}"
is_migration_crash() {
  docker logs --tail 80 "$1" 2>&1 | grep -qiE "$MIGRATION_RE"
}

# safe_restart <container> <reason> — the only path that restarts a container.
#   honours migration detection, $WD_MAX_RESTARTS/hour and backoff.
safe_restart() {
  local c="$1" why="$2" f="$WD_STATE_DIR/restarts-$1" t n last wait
  t=$(now)
  if [ -f "$WD_STATE_DIR/migration-$c" ]; then
    log "$c: migration crash already flagged — not restarting"; return 1
  fi
  if is_migration_crash "$c"; then
    docker update --restart=no "$c" >/dev/null 2>&1
    echo "$t" > "$WD_STATE_DIR/migration-$c"
    ALERT "migration-$c" ":rotating_light: $c is crash-looping on a DB migration error (alembic / revision) — restart policy set to no, NOT restarting. Fix the migration/branch, then rm $WD_STATE_DIR/migration-$c and docker update --restart=unless-stopped $c"
    return 1
  fi
  # keep only the last hour of restart timestamps
  if [ -f "$f" ]; then awk -v c=$((t-3600)) '$1>=c' "$f" > "$f.tmp" && mv "$f.tmp" "$f"; fi
  n=$(wc -l < "$f" 2>/dev/null || echo 0); n=${n// /}
  if [ "$n" -ge "$WD_MAX_RESTARTS" ]; then
    ALERT "cap-$c" ":rotating_light: $c hit the restart cap ($n/$WD_MAX_RESTARTS in 1h, last reason: $why) — watchdog stopped restarting it; needs a human"
    return 1
  fi
  if [ "$n" -gt 0 ]; then
    last=$(tail -1 "$f")
    wait=$(echo "$WD_BACKOFF" | awk -v i="$n" '{print $i}')
    [ -z "$wait" ] && wait=$(echo "$WD_BACKOFF" | awk '{print $NF}')
    if [ $(( t - last )) -lt "$wait" ]; then
      log "$c: backoff (${wait}s after restart #$n) — skipping this cycle"; return 1
    fi
  fi
  echo "$t" >> "$f"
  log "restarting $c ($why) — attempt $((n+1))/$WD_MAX_RESTARTS this hour"
  docker start "$c" >/dev/null 2>&1 || docker restart "$c" >/dev/null 2>&1
}
# 1. containers stuck in Created / Restarting, or OOM-killed and stopped
for c in $CORE; do
  st=$(docker inspect -f '{{.State.Status}} {{.State.OOMKilled}}' "$c" 2>/dev/null || echo "missing false")
  status=${st% *}
  oom=${st##* }
  case "$status" in
    created|restarting|exited|dead)
      safe_restart "$c" "$status" && ALERT "stuck-$c" ":warning: $c was $status — restarted";;
  esac
  if [ "$oom" = "true" ] && [ "$status" != "running" ]; then
    safe_restart "$c" "oom" && ALERT "oom-$c" ":warning: $c was OOM-killed — restarted (check memory pressure)"
  fi
done
# 2. Redis auth drift — env password must ping live redis
P=$(grep -oP '^REDIS_PASSWORD=\K.*' .env 2>/dev/null)
if [ -n "$P" ] && ! docker exec redis redis-cli -a "$P" ping 2>/dev/null | grep -q PONG; then
  ACTUAL=$(docker inspect redis --format '{{json .Config.Cmd}}' | grep -oP '(?<=requirepass",")[A-Za-z0-9_-]+' | head -1)
  if [ -n "$ACTUAL" ]; then
    sed -i "s|^REDIS_PASSWORD=.*|REDIS_PASSWORD=$ACTUAL|" .env
    ALERT "redis-pw" ":rotating_light: REDIS_PASSWORD drifted — synced .env to live redis; recreate workers"
  fi
fi
# 3. worker nodes answering ping (expect 4)
N=$(docker compose exec -T social-worker-publishing celery -A app.worker.celery_app inspect ping 2>/dev/null | grep -c pong)
if [ "$N" -lt 4 ]; then
  ALERT "celery-nodes" ":warning: only $N/4 celery nodes answering — check workers"
fi
# 4. LinkedIn sidecar session — warn if it dropped
LI=$(docker compose exec -T social-api python -c "
import asyncio
from app.services.linkedin_sidecar import LinkedInSidecarClient
async def m():
  s = await LinkedInSidecarClient().check_session()
  print(s.get('logged_in'))
asyncio.run(m())" 2>/dev/null | tail -1)
[ "$LI" = "False" ] && ALERT "li-session" ":warning: LinkedIn sidecar session dropped — needs re-auth"
# 5. DMR lock starvation — gpu:dmr_lock (TTL 660s) held at 3+ consecutive
#    checks means every embed/generate call is stalling behind contention
#    or a leaked holder. Self-heals at TTL; we surface it before that.
if [ -n "${P:-}" ]; then
  LK=$(docker exec redis redis-cli -a "$P" pttl gpu:dmr_lock 2>/dev/null || echo -2)
  LKF="$WD_STATE_DIR/dmr-lock-seen"
  if [ "${LK:-0}" -gt 0 ]; then
    n=$(( $(cat "$LKF" 2>/dev/null || echo 0) + 1 ))
    echo "$n" > "$LKF"
    [ "$n" -ge 3 ] && ALERT "dmr-lock" ":warning: gpu:dmr_lock held at $n consecutive checks (~$((n*10))min) — DMR calls stalling; look for a wedged holder"
  else
    rm -f "$LKF"
  fi
fi
exit 0

# 9. env-drift creds check — OAuth client secrets/IDs get wiped from .env
#    during rotations (seen 2026-10-28: LINKEDIN_CLIENT_ID empty → 400s).
#    Compare .env vs the container's live env — flag empties + mismatches.
D=$(docker exec social-api python - <<'PY' 2>/dev/null
import os
want = ["LINKEDIN_CLIENT_ID","LINKEDIN_CLIENT_SECRET","TWITTER_CLIENT_ID",
        "TWITTER_CLIENT_SECRET","SLACK_BOT_TOKEN"]
bad = [k for k in want if not (os.environ.get(k) or "").strip()]
print(",".join(bad))
PY
)
[ -n "$D" ] && ALERT "env-drift" ":warning: empty creds in container env: $D — check .env vs secondary checkout"
exit 0

# 10. gateway upstream — when social-api recreates, its IP changes and the
#     nginx gateway keeps proxying to the stale one (8083 dead while API
#     is fine internally — seen 2026-10-28). Heal = restart gateway, not api.
G=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 http://localhost:8083/health 2>/dev/null || echo 000)
D=$(docker exec social-api curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://localhost:8000/health 2>/dev/null || echo 000)
if [ "$D" = "200" ] && [ "$G" != "200" ]; then
  safe_restart social-api-gateway "stale-upstream" && ALERT "gateway-upstream" ":warning: gateway had stale upstream — restarted"
fi
# 11. n8n unhealthy — stuck "health: starting" blocks compose up -d on
#     dependent services. Restart clears it.
N8=$(docker inspect -f '{{.State.Health.Status}}' n8n 2>/dev/null || echo missing)
if [ "$N8" = "unhealthy" ]; then
  safe_restart n8n "unhealthy" && ALERT "n8n-unhealthy" ":warning: n8n unhealthy — restarted"
fi
exit 0
