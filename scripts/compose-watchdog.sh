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
  local key="$1" msg="$2"
  local f="/tmp/wd-$(echo "$key" | md5sum | cut -c1-8)"
  [ -f "$f" ] && [ $(( $(date +%s) - $(stat -c %Y "$f") )) -lt 1800 ] && return
  timeout 20 docker compose exec -T social-api python -c "
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
# 6. Draft posts sitting >24h with empty media_ids — violates the
#    correct-media rule at publish time; surface for review, don't
#    auto-attach (only a human/agent can judge *correct* media).
EMPTY_MEDIA=$(docker exec social-postgres psql -U social_user -d social_automation -tAc \
  "SELECT count(*) FROM posts WHERE status='draft' AND (media_ids='{}' OR media_ids IS NULL) AND created_at < now() - interval '24 hours'" 2>/dev/null || echo 0)
if [ "${EMPTY_MEDIA:-0}" -gt 0 ]; then
  ALERT "empty-media-drafts" ":warning: $EMPTY_MEDIA draft post(s) older than 24h have no media — attach correct media before scheduling"
fi
# 7. Scheduled-post orphans — a post marked 'scheduled' with NO pending/
#    processing publish_queue row never publishes (the worker scans the
#    queue, not posts.scheduled_at). Heal by enqueueing the missing rows.
ORPHANS=$(docker exec social-postgres psql -U social_user -d social_automation -tAc "
INSERT INTO publish_queue (id, post_id, social_account_id, scheduled_at, priority, attempts, max_attempts, status, created_at)
SELECT gen_random_uuid(), p.id, t.social_account_id, p.scheduled_at, 5, 0, 3, 'pending', now()
FROM posts p JOIN post_targets t ON t.post_id=p.id
WHERE p.status='scheduled' AND p.scheduled_at IS NOT NULL
  AND t.status NOT IN ('published','skipped','failed')
  AND NOT EXISTS (SELECT 1 FROM publish_queue q WHERE q.post_id=p.id
                  AND q.social_account_id=t.social_account_id AND q.status IN ('pending','processing'))
ON CONFLICT DO NOTHING
RETURNING 1" 2>/dev/null | grep -c 1)
[ "${ORPHANS:-0}" -gt 0 ] && ALERT "queue-orphans" ":warning: healed $ORPHANS scheduled post(s) that had no publish_queue row — they would never have published"
# 8. Wedged publishes — posts stuck 'publishing' >30min with no active
#    queue row are dead (worker crashed mid-attempt): reconcile to failed.
WEDGED=$(docker exec social-postgres psql -U social_user -d social_automation -tAc "
UPDATE posts SET status='failed', failed_at=now(),
       failure_reason='watchdog: stuck in publishing >30min with no active queue row'
WHERE status='publishing' AND updated_at < now() - interval '30 minutes'
  AND NOT EXISTS (SELECT 1 FROM publish_queue q WHERE q.post_id=posts.id
                  AND q.status IN ('pending','processing'))
RETURNING 1" 2>/dev/null | grep -c 1)
[ "${WEDGED:-0}" -gt 0 ] && ALERT "wedged-publish" ":warning: marked $WEDGED post(s) failed — stuck 'publishing' >30min with no active queue row"
# 8b. Stale processing locks — belt & suspenders alongside the in-code
#     stale-lock reclaim: a processing row locked >45min is a zombie — LinkedIn org image publishes legit take ~17-25min, 15min killed healthy publishes (2026-10-28).
STALE=$(docker exec social-postgres psql -U social_user -d social_automation -tAc "
UPDATE publish_queue SET status='pending', locked_at=NULL, locked_by=NULL
WHERE status='processing' AND locked_at < now() - interval '45 minutes'
RETURNING 1" 2>/dev/null | grep -c 1)
[ "${STALE:-0}" -gt 0 ] && ALERT "stale-qlock" ":warning: released $STALE stale publish_queue lock(s) — retry will pick them up"
# 9. Publish-verify — a queue slot that fired >2h ago whose target is still
#    'pending' means the platform-side path rejected/dropped it silently.
#    Alert fast; don't wait for tomorrow's digest. Recurring signatures get
#    a remediation note per the proactivity principle.
LATE=$(docker exec social-postgres psql -U social_user -d social_automation -tAc "
SELECT string_agg(DISTINCT sa.platform, ', ')
FROM publish_queue pq
JOIN post_targets t ON t.post_id=pq.post_id AND t.social_account_id=pq.social_account_id
JOIN social_accounts sa ON sa.id=pq.social_account_id
WHERE pq.status='completed' AND pq.scheduled_at < now() - interval '2 hours'
  AND t.status='pending'" 2>/dev/null)
[ -n "${LATE:-}" ] && ALERT "publish-verify" ":warning: platform(s) ${LATE} show pending >2h after queue completed — check publish errors in UI"
exit 0
