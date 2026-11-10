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

# Feed every alert into the ops-agent incident table (ops_incidents) so the
# AgentOps sweep can RCA/escalate it. Best-effort: pre-merge the CLI doesn't
# exist in the container yet, and a down api can't take reports anyway.
INCIDENT() {  # INCIDENT <kind> <severity> <title>
  docker exec social-api python3 /app/scripts/ops_incident.py report \
    --source compose-watchdog --kind "$1" --severity "$2" \
    --title "$3" --detail "{\"watchdog_key\":\"$1\"}" >/dev/null 2>&1 \
  || "$REPO_DIR/scripts/ops_incident_host.sh" compose-watchdog "$1" "$2" "$3" >/dev/null 2>&1 || true
}
ALERT() {  # post to the ops Slack channel, throttled to once/30min per key
  local key="$1" msg="$2"
  local f
  f="$WD_STATE_DIR/alert-$(echo "$key" | md5sum | cut -c1-8)"
  log "ALERT[$key] $msg"
  case "$msg" in *rotating_light*) INCIDENT "$key" critical "$msg" ;; *) INCIDENT "$key" ops "$msg" ;; esac
  [ -f "$f" ] && [ $(( $(date +%s) - $(stat -c %Y "$f") )) -lt 1800 ] && return
  docker compose exec -T social-api python -c "
import asyncio
from app.services.slack_notifications import post_alert_to_slack
asyncio.run(post_alert_to_slack('$msg'))" >/dev/null 2>&1 && touch "$f" || {
  # api down — fall back to the raw webhook so the outage still alerts
  [ -n "${SLACK_ALERTS_WEBHOOK_URL:-}" ] && curl -sf -X POST "$SLACK_ALERTS_WEBHOOK_URL" -H 'Content-Type: application/json' -d "{\"text\":\"$msg\"}" >/dev/null 2>&1 && touch "$f" || true
}
}
CORE="social-api social-worker-default social-worker-publishing social-worker-media social-worker-messenger celery-beat redis social-postgres social-cloudflared"
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
# Mount-broken containers (exit 127, "error mounting" on bind-mounted
# files — Docker Desktop bind-mount cache drift) can never be fixed by
# start/restart; they need a compose force-recreate (seen 2026-11-10 on
# social-cloudflared → Cloudflare Tunnel 1033 → all OAuth callbacks dead ~2h).
is_mount_broken() {
  docker logs --tail 30 "$1" 2>&1 | grep -qiE "error mounting|not a directory.*(mount|bind)|failed to create shim task"
}
recreate_container() {
  local c="$1" svc
  svc=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' "$c" 2>/dev/null || true)
  case "$svc" in ""|*" "*) log "$c: no compose service label — cannot recreate"; return 1;; esac
  log "recreating $c (compose service $svc — mount-broken)"
  docker compose up -d --no-deps --force-recreate "$svc" >/dev/null 2>&1
}
# EACCES on a bind-mounted file — the WSL/Windows dual-checkout flips mount
# ownership between uid 1000/1001, so the image user can't read 600-perm
# files (n8n .n8n/config crash-looped twice on 2026-11-10). Restart can't
# fix it: chown the host mount to the container's uid, then recreate.
is_eacces_crash() {
  docker logs --tail 60 "$1" 2>&1 | grep -qE 'EACCES|permission denied'
}
eacces_host_path() {
  docker logs --tail 60 "$1" 2>&1 | grep -E 'EACCES|permission denied' \
    | grep -oP "'/[^']+'" | head -1 | tr -d "'"
}
container_uid_gid() {
  local c="$1" u g img
  u=$(docker exec "$c" id -u 2>/dev/null); g=$(docker exec "$c" id -g 2>/dev/null)
  if [ -z "$u" ] || [ -z "$g" ]; then
    img=$(docker inspect -f '{{.Config.Image}}' "$c" 2>/dev/null)
    [ -n "$img" ] || return 1
    u=$(docker run --rm --entrypoint id "$img" -u 2>/dev/null)
    g=$(docker run --rm --entrypoint id "$img" -g 2>/dev/null)
  fi
  [ -n "$u" ] && [ -n "$g" ] && echo "$u:$g"
}
# fix_eacces <container> — chown the offending mount + recreate. Capped at
# 2 attempts/hour per container so a persistent perms problem alerts
# instead of looping.
fix_eacces() {
  local c="$1" path src ug f="$WD_STATE_DIR/eacces-$1" t n
  t=$(now)
  if [ -f "$f" ]; then awk -v c=$((t-3600)) '$1>=c' "$f" > "$f.tmp" && mv "$f.tmp" "$f"; fi
  n=$(wc -l < "$f" 2>/dev/null || echo 0); n=${n// /}
  if [ "$n" -ge 2 ]; then
    ALERT "eacces-cap-$c" ":rotating_light: $c EACCES fix attempted $n times in 1h — chown isn't holding; needs a human"
    return 1
  fi
  path=$(eacces_host_path "$c"); [ -n "$path" ] || return 1
  # longest matching mount destination wins
  src=$(docker inspect -f '{{range .Mounts}}{{.Destination}}|{{.Source}}{{"\n"}}{{end}}' "$c" 2>/dev/null \
        | awk -F'|' -v p="$path" 'index(p,$1)==1 {if (length($1)>m) {m=length($1); s=$2}} END{print s}')
  { [ -n "$src" ] && [ -d "$src" ]; } || { log "$c: EACCES path $path not under a bind mount"; return 1; }
  ug=$(container_uid_gid "$c"); [ -n "$ug" ] || { log "$c: cannot resolve container uid/gid"; return 1; }
  echo "$t" >> "$f"
  log "$c: EACCES on $path — chown $src -> $ug"
  docker run --rm -v "$src:/fix" alpine chown -R "$ug" /fix >/dev/null 2>&1 || return 1
  recreate_container "$c"
}
# Services beyond CORE that must stay up — same stuck/exited/dead checks.
# comfyui-gpu and other sleepable/on-demand containers are deliberately
# absent; stack-ops has its own port-binding probe below.
EXTRA="n8n n8n-task-runner infisical social-api-gateway social-frontend env-manager-backend env-manager-frontend mautic nextcloud-mcp-server facebook-browser-sidecar tiktok-browser-sidecar messenger-sidecar linkedin-browser-sidecar browser-novnc instagram-private-api flower"
# 1. containers stuck in Created / Restarting, or OOM-killed and stopped
for c in $CORE $EXTRA; do
  st=$(docker inspect -f '{{.State.Status}} {{.State.OOMKilled}}' "$c" 2>/dev/null || echo "missing false")
  status=${st% *}
  oom=${st##* }
  case "$status" in
    created|restarting|exited|dead)
      if [ "$status" = "exited" ] || [ "$status" = "dead" ]; then
        if is_mount_broken "$c"; then
          recreate_container "$c" && ALERT "remount-$c" ":warning: $c exited on a broken bind-mount — force-recreated"
          continue
        fi
      fi
      if { [ "$status" = "restarting" ] || [ "$status" = "exited" ] || [ "$status" = "dead" ]; } \
         && is_eacces_crash "$c" && fix_eacces "$c"; then
        ALERT "eacces-$c" ":warning: $c crash-looping on EACCES (uid-drifted bind mount) — chowned mount to container uid and recreated"
        continue
      fi
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
# 3b. ops-agent dead-man — the sweep heartbeats ops_agent:last_sweep every
#     5 min; >20 min stale means triage is silently dead. Missing key =
#     feature not yet deployed, skip (celery-nodes check covers beat).
if [ -n "${P:-}" ]; then
  LS=$(docker exec redis redis-cli -a "$P" get ops_agent:last_sweep 2>/dev/null || echo "")
  case "$LS" in
    ''|*[!0-9]*) : ;;
    *) [ $(( $(now) - LS )) -gt 1200 ] && ALERT "ops-agent-deadman" ":warning: ops-agent sweep stale >20min (last $(date -u -d "@$LS" +%H:%M)Z) — triage is dead, check celery-beat/worker-default";;
  esac
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
# 12. published-port bindings — after Docker Desktop flaps the engine can
#     leave stack-ops "running/healthy" while its userland port proxy loses
#     the bindings (seen 2026-11-10: localhost:8011/9223/8787 all 000 with
#     every container Up). Probe the diagnostic surface; dead ports with a
#     healthy stack-ops container = restart stack-ops to rebind.
SO=$(docker inspect -f '{{.State.Health.Status}}' stack-ops 2>/dev/null || echo missing)
if [ "$SO" = "healthy" ] || [ "$SO" = "running" ]; then
  DEAD=""
  for pp in 8011 8787 9223; do
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "http://localhost:$pp/" 2>/dev/null || echo 000)
    [ "$code" = "000" ] && DEAD="$DEAD $pp"
  done
  if [ -n "$DEAD" ]; then
    safe_restart stack-ops "port-bindings-dead:$DEAD" && ALERT "port-bindings" ":warning: stack-ops published ports unresponsive ($DEAD) — restarted to rebind"
  fi
fi
# 13. generic published-port probes — after Docker Desktop flaps, ANY
#     container can keep running/healthy while its userland port proxy
#     loses the bindings (2026-11-10: every host port dead at once).
#     A dead port on a running container = force-recreate to rebind.
#     container:port pairs; override with WD_PORT_PROBES.
PORT_PROBES="${WD_PORT_PROBES:-n8n:5678 infisical:8090 social-api:8083 social-frontend:8082 env-manager-backend:8085 mautic:8021 nextcloud-mcp-server:9231}"
for pp in $PORT_PROBES; do
  pc=${pp%%:*}; pport=${pp##*:}
  pst=$(docker inspect -f '{{.State.Status}}' "$pc" 2>/dev/null || echo missing)
  [ "$pst" = "running" ] || continue
  pcode=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "http://localhost:$pport/" 2>/dev/null || echo 000)
  [ "$pcode" = "000" ] || continue
  # recreate at most once per 30 min per container — a genuinely dead
  # service must alert, not churn
  pf="$WD_STATE_DIR/portfix-$pc"; pt=$(now)
  if [ -f "$pf" ] && [ $(( pt - $(cat "$pf" 2>/dev/null || echo 0) )) -lt 1800 ]; then
    continue
  fi
  echo "$pt" > "$pf"
  recreate_container "$pc" && ALERT "port-dead-$pc" ":warning: $pc published port :$pport unresponsive — force-recreated to rebind"
done
exit 0
