#!/usr/bin/env bash
# SocialAuto stack watchdog — catches the failure modes that need a
# human today. Host-level (outside compose) so it sees stack failures.
# Run every 5 min via cron: */5 * * * * /home/tbaltzakis/cu130-slim/scripts/compose-watchdog.sh
set -u
cd /home/tbaltzakis/cu130-slim
ALERT() {  # post to the ops Slack channel, throttled to once/30min per key
  local key="$1" msg="$2" f="/tmp/wd-$(echo "$key" | md5sum | cut -c1-8)"
  [ -f "$f" ] && [ $(( $(date +%s) - $(stat -c %Y "$f") )) -lt 1800 ] && return
  docker compose exec -T social-api python -c "
import asyncio
from app.services.slack_notifications import post_alert_to_slack
asyncio.run(post_alert_to_slack('$msg'))" >/dev/null 2>&1 && touch "$f" || {
  # api down — fall back to the raw webhook so the outage still alerts
  [ -n "${SLACK_ALERTS_WEBHOOK_URL:-}" ] && curl -sf -X POST "$SLACK_ALERTS_WEBHOOK_URL"     -H 'Content-Type: application/json' -d "{"text":"$msg"}" >/dev/null 2>&1 && touch "$f" || true
}
}
# 1. containers stuck in Created / Restarting
for c in social-api social-worker-default social-worker-publishing social-worker-media social-worker-messenger celery-beat redis; do
  st=$(docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null || echo missing)
  case "$st" in
    created|restarting|exited|dead)
      docker start "$c" >/dev/null 2>&1 && ALERT "stuck-$c" ":warning: $c was $st — restarted";;
  esac
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
exit 0
