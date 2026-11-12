#!/usr/bin/env bash
# master-deploy-watch.sh — deploy origin/master to the running stack.
#
# Agents merge PRs to master continuously; nothing used to pull + restart,
# so fixes sat undeployed until a human/agent noticed. This watcher closes
# the loop: every run it fetches master, and when HEAD advanced it takes
# the deploy lock, fast-forward pulls, restarts the services whose code
# changed, health-gates, and alerts on failure.
#
# Schedule: Windows task "SocialAuto-MasterDeploy" every 10 min:
#   C:\Windows\System32\wsl.exe -d Ubuntu-24.04 -e bash -lc \
#     "/home/tbaltzakis/cu130-slim/scripts/master-deploy-watch.sh"
# Same conventions as compose-watchdog.sh: respects .deploy.lock, logs to
# logs/deploy-watch.log, Slack-alerts throttled per key.
set -u
cd "$(dirname "$0")/.." || exit 1
REPO_DIR="$(pwd)"

DW_STATE_DIR="${DW_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/master-deploy-watch}"
DW_LOG="${DW_LOG:-$REPO_DIR/logs/deploy-watch.log}"
DEPLOY_LOCK="${DEPLOY_LOCK:-$REPO_DIR/.deploy.lock}"
DEPLOY_LOCK_MAX_AGE="${DEPLOY_LOCK_MAX_AGE:-3600}"
DW_MAX_FAILS="${DW_MAX_FAILS:-3}"        # health-gate failures before giving up on a sha
mkdir -p "$DW_STATE_DIR" "$(dirname "$DW_LOG")"
exec >>"$DW_LOG" 2>&1

now() { date +%s; }
log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [deploy-watch] $*"; }
mtime() { stat -c %Y "$1" 2>/dev/null || echo 0; }

if [ -z "${SLACK_ALERTS_WEBHOOK_URL:-}" ] && [ -f .env ]; then
  SLACK_ALERTS_WEBHOOK_URL=$(grep -oP '^SLACK_ALERTS_WEBHOOK_URL=\K.*' .env 2>/dev/null | tr -d '"' | head -1)
fi
ALERT() {  # throttled to once/30min per key
  local key="$1" msg="$2"
  local f="$DW_STATE_DIR/alert-$(echo "$key" | md5sum | cut -c1-8)"
  log "ALERT[$key] $msg"
  [ -f "$f" ] && [ $(( $(now) - $(mtime "$f") )) -lt 1800 ] && return
  docker compose exec -T social-api python -c "
import asyncio
from app.services.slack_notifications import post_alert_to_slack
asyncio.run(post_alert_to_slack('$msg'))" >/dev/null 2>&1 && touch "$f" || {
    [ -n "${SLACK_ALERTS_WEBHOOK_URL:-}" ] && curl -sf -X POST "$SLACK_ALERTS_WEBHOOK_URL" -H 'Content-Type: application/json' -d "{\"text\":\"$msg\"}" >/dev/null 2>&1 && touch "$f" || true
  }
}

CORE_RESTART="social-api social-worker-publishing social-worker-media social-worker-default social-worker-messenger celery-beat"
HEALTH_URL="${HEALTH_URL:-http://localhost:8083/health}"

# ── deploy lock held by someone else → hands off ──────────────────────
if [ -f "$DEPLOY_LOCK" ]; then
  age=$(( $(now) - $(mtime "$DEPLOY_LOCK") ))
  if [ "$age" -lt "$DEPLOY_LOCK_MAX_AGE" ]; then
    log "deploy lock present (${age}s old) — skipping"
    exit 0
  fi
  log "deploy lock stale (${age}s) — ignoring"
fi

# ── engine down → nothing to do (compose-watchdog owns engine restarts)
if ! docker info >/dev/null 2>&1; then
  log "docker engine unreachable — skipping"
  exit 0
fi

git fetch origin master --quiet 2>/dev/null || { log "git fetch failed — skipping"; exit 0; }
new=$(git rev-parse origin/master)
last=$(cat "$DW_STATE_DIR/last_sha" 2>/dev/null || echo "")
[ "$new" = "$last" ] && exit 0

# agents leave the checkout on topic branches — always deploy from master
if [ "$(git rev-parse --abbrev-ref HEAD)" != "master" ]; then
  if ! git switch master --quiet >>"$DW_LOG" 2>&1; then
    ALERT "not-on-master" ":warning: master-deploy-watch: could not switch checkout to master — NOT deploying $new"
    exit 0
  fi
  log "switched checkout to master"
fi

# first run: adopt current HEAD without deploying (avoids a surprise deploy
# of whatever accumulated while the watcher didn't exist)
if [ -z "$last" ]; then
  git rev-parse HEAD > "$DW_STATE_DIR/last_sha"
  log "baseline set to $(git rev-parse --short HEAD) — watching for new merges"
  exit 0
fi

# NOTE: no dirty-tree pre-check — `git merge --ff-only` below is itself the
# guard: it refuses (and we alert) only when incoming changes would touch
# uncommitted files. Unrelated agent WIP must not block deploys.

# ── take the lock for the whole deploy ────────────────────────────────
echo "$(date -u +%FT%TZ) master-deploy-watch $new" > "$DEPLOY_LOCK"
trap 'rm -f "$DEPLOY_LOCK"' EXIT

log "master advanced $last -> $new — deploying"
changed=$(git diff --name-only "$last" "$new" 2>/dev/null || git diff --name-only HEAD~1 HEAD)

held=""
stash_msg=""

if ! git merge --ff-only origin/master >>"$DW_LOG" 2>&1; then
  # ── auto-hold ONLY the WIP files this merge would touch, then retry ──
  # Agents keep uncommitted work in the shared checkout; a blanket dirty
  # check would freeze deploys forever. git itself tells us exactly which
  # paths block: dirty tracked files + untracked files that incoming
  # commits add. We stash/move just those, merge, then restore — the
  # agent's work survives in a named stash / .dw-held sibling file.
  incoming=$(git diff --name-only HEAD origin/master 2>/dev/null || true)
  held=""
  stash_msg=""
  if [ -n "$incoming" ]; then
    mapfile -t tb < <(git diff HEAD --name-only 2>/dev/null | grep -Fxf <(printf '%s
' "$incoming") || true)
    if [ "${#tb[@]}" -gt 0 ]; then
      stash_msg="dw-auto-$(date +%s)"
      git stash push -q -m "$stash_msg" -- "${tb[@]}" 2>/dev/null && held="$held tracked:${#tb[@]}"
    fi
    mapfile -t ub < <(git ls-files --others --exclude-standard | grep -Fxf <(printf '%s
' "$incoming") || true)
    for f in "${ub[@]:-}"; do
      [ -z "$f" ] && continue
      mv "$f" "$f.dw-held" 2>/dev/null && held="$held untracked:$f"
    done
  fi
  if git merge --ff-only origin/master >>"$DW_LOG" 2>&1; then
    log "merge succeeded after auto-holding WIP ($held)"
    [ -n "$held" ] && ALERT "wip-held" ":information_source: master-deploy-watch auto-held uncommitted files to deploy $new ($held) — work preserved in stash '$stash_msg' / *.dw-held; owning agent should commit."
  else
    ALERT "pull-failed" ":rotating_light: master-deploy-watch: ff-pull of $new failed even after auto-holding WIP — diverged master; needs a human"
    exit 1
  fi
fi

# ── restore auto-held WIP (stash pop may conflict — stash stays safe) ──
if [ -n "$stash_msg" ]; then
  top=$(git stash list --format=%s 2>/dev/null | head -1)
  case "$top" in
    "$stash_msg"*)
      if git stash pop -q >>"$DW_LOG" 2>&1; then
        log "auto-held stash restored"
      else
        ALERT "stash-pop-conflict" ":warning: master-deploy-watch: auto-held stash '$stash_msg' did not pop cleanly — work is SAFE in git stash; owning agent must resolve"
      fi ;;
  esac
fi
find . -maxdepth 6 -name '*.dw-held' 2>/dev/null | while read -r h; do
  orig="${h%.dw-held}"
  if [ -f "$orig" ] && cmp -s "$h" "$orig"; then rm -f "$h"; else mv -f "$h" "$orig"; fi
done

app_code=0; compose_change=0
case "$changed" in
  *social-automation/backend/app/*|*social-automation/backend/alembic/*|*social-automation/backend/scripts/*) app_code=1 ;;
esac
case "$changed" in
  *docker-compose*.y*ml|*compose*.y*ml|*.env*|Dockerfile*) compose_change=1 ;;
esac

if [ "$compose_change" = 1 ]; then
  log "compose/env files changed — docker compose up -d"
  docker compose up -d >>"$DW_LOG" 2>&1 || ALERT "compose-up" ":rotating_light: master-deploy-watch: compose up -d failed after $new"
fi
if [ "$app_code" = 1 ]; then
  log "backend code changed — restarting $CORE_RESTART"
  docker compose restart $CORE_RESTART >>"$DW_LOG" 2>&1 || ALERT "restart" ":rotating_light: master-deploy-watch: compose restart failed after $new"
fi

# ── health gate: api + all 4 celery nodes ─────────────────────────────
sleep 25
fails_file="$DW_STATE_DIR/fails-$new"
fails=0; [ -f "$fails_file" ] && fails=$(cat "$fails_file")
healthy=1
curl -sf --max-time 10 "$HEALTH_URL" >/dev/null || healthy=0
nodes=$(docker compose exec -T social-worker-publishing celery -A app.worker.celery_app inspect ping 2>/dev/null | grep -c "pong" || true)
[ "${nodes:-0}" -lt 4 ] && healthy=0

if [ "$healthy" = 1 ]; then
  echo "$new" > "$DW_STATE_DIR/last_sha"
  rm -f "$fails_file"
  log "deployed $(git rev-parse --short "$new") — healthy (celery nodes: $nodes)"
else
  fails=$((fails+1)); echo "$fails" > "$fails_file"
  log "health gate failed ($fails/$DW_MAX_FAILS) after $new"
  if [ "$fails" -ge "$DW_MAX_FAILS" ]; then
    echo "$new" > "$DW_STATE_DIR/last_sha"
    rm -f "$fails_file"
    ALERT "deploy-unhealthy" ":rotating_light: master-deploy-watch: $new deployed but health gate failed $fails times — api:$HEALTH_URL celery:$nodes/4. Check logs; watchdog migration guard may have disabled restarts."
  else
    ALERT "deploy-retry" ":warning: master-deploy-watch: $new deployed but health gate failed (attempt $fails/$DW_MAX_FAILS) — will retry next run"
  fi
fi
