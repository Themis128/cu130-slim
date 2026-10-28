#!/usr/bin/env bash
# SocialAuto stack watchdog — catches the failure modes that need a
# human today. Host-level (outside compose) so it sees stack failures.
# Every 10 min via scheduled task (Windows) or cron (Linux):
#   conhost.exe --headless "C:\Program Files\Git\bin\bash.exe" -lc '~/cu130-slim/scripts/compose-watchdog.sh'
set -u
cd "$(dirname "$0")/.."

# 0. Docker Desktop backend dead? (npipe gone / engine unresponsive —
#    every docker call would fail below anyway). `docker desktop restart`
#    is the documented fix: ~20s, containers return on restart policies.
#    Throttled to one restart attempt per 30min via the ALERT state file.
DD_RESTART_FLAG="/tmp/wd-dd-restart"
if ! docker info >/dev/null 2>&1; then
  echo "[watchdog] docker engine unreachable"
  if [ ! -f "$DD_RESTART_FLAG" ] || [ $(( $(date +%s) - $(stat -c %Y "$DD_RESTART_FLAG" 2>/dev/null || echo 0) )) -ge 1800 ]; then
    touch "$DD_RESTART_FLAG"
    DD_CLI="/c/Program Files/Docker/Docker/resources/bin/docker.exe"
    [ -x "$DD_CLI" ] || DD_CLI="docker"
    if "$DD_CLI" desktop restart >/dev/null 2>&1; then
      echo "[watchdog] docker desktop restart issued — engine returns in ~20s"
    else
      echo "[watchdog] docker desktop restart failed — needs manual attention"
    fi
  fi
  exit 0
fi

ALERT() {  # post to the ops Slack channel, throttled to once/30min per key
  local key="$1" msg="$2" f="/tmp/wd-$(echo "$key" | md5sum | cut -c1-8)"
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
# 1. containers stuck in Created / Restarting, or OOM-killed and stopped
for c in $CORE; do
  st=$(docker inspect -f '{{.State.Status}} {{.State.OOMKilled}}' "$c" 2>/dev/null || echo "missing false")
  status=${st% *}
  oom=${st##* }
  case "$status" in
    created|restarting|exited|dead)
      docker start "$c" >/dev/null 2>&1 && ALERT "stuck-$c" ":warning: $c was $status — restarted";;
  esac
  if [ "$oom" = "true" ] && [ "$status" != "running" ]; then
    docker start "$c" >/dev/null 2>&1 && ALERT "oom-$c" ":warning: $c was OOM-killed — restarted (check memory pressure)"
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
  LKF="/tmp/wd-dmr-lock-seen"
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
  docker restart social-api-gateway >/dev/null 2>&1 && ALERT "gateway-upstream" ":warning: gateway had stale upstream — restarted"
fi
# 11. n8n unhealthy — stuck "health: starting" blocks compose up -d on
#     dependent services. Restart clears it.
N8=$(docker inspect -f '{{.State.Health.Status}}' n8n 2>/dev/null || echo missing)
if [ "$N8" = "unhealthy" ]; then
  docker restart n8n >/dev/null 2>&1 && ALERT "n8n-unhealthy" ":warning: n8n unhealthy — restarted"
fi
exit 0
