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
# Pin every compose call to the LIVE project (deploy/socialauto-standalone).
# The repo root has its own docker-compose.yml defining the same service
# names under a different project - unpinned calls from the root conflict
# with the running containers (redis name-in-use on force-recreate).
COMPOSE_FILE="$REPO_DIR/deploy/socialauto-standalone/docker-compose.yml"
COMPOSE_CTX="$REPO_DIR/deploy/socialauto-standalone"
COMPOSE() { docker compose -f "$COMPOSE_FILE" --project-directory "$COMPOSE_CTX" "$@"; }

DW_STATE_DIR="${DW_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/master-deploy-watch}"
DW_LOG="${DW_LOG:-$REPO_DIR/logs/deploy-watch.log}"
DEPLOY_LOCK="${DEPLOY_LOCK:-$REPO_DIR/.deploy.lock}"
DEPLOY_LOCK_MAX_AGE="${DEPLOY_LOCK_MAX_AGE:-3600}"
DW_MAX_FAILS="${DW_MAX_FAILS:-3}"        # health-gate failures before giving up on a sha
DW_MAX_DEFERS="${DW_MAX_DEFERS:-6}"      # busy-defer cycles (~10min cadence) before deploying anyway
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
  COMPOSE exec -T social-api python -c "
import asyncio
from app.services.slack_notifications import post_alert_to_slack
asyncio.run(post_alert_to_slack('$msg'))" >/dev/null 2>&1 && touch "$f" || {
    [ -n "${SLACK_ALERTS_WEBHOOK_URL:-}" ] && curl -sf -X POST "$SLACK_ALERTS_WEBHOOK_URL" -H 'Content-Type: application/json' -d "{\"text\":\"$msg\"}" >/dev/null 2>&1 && touch "$f" || true
  }
}

CORE_RESTART="social-api social-worker-publishing social-worker-media social-worker-default social-worker-messenger social-worker-meta celery-beat"
# Split by live-container ownership: api/beat/messenger run as
# cu130-slim-project containers (the root compose also declares them),
# so the standalone file can never create/recreate those names — a plain
# `up -d` or --force-recreate over the full list dies on "name already in
# use" mid-run, leaving <hash>_ replace= stubs and half-swapped workers.
# Pull covers every tag (project-agnostic); recreates run per-owner.
CORE_STANDALONE="social-worker-publishing social-worker-media social-worker-default social-worker-meta"
CORE_ROOT="social-api celery-beat social-worker-messenger"
# Services whose live containers the root cu130-slim project owns — excluded
# from standalone `up -d` so compose/env changes cannot abort mid-deploy.
ROOT_OWNED="social-api celery-beat social-worker-messenger messenger-sidecar facebook-browser-sidecar"
ROOT_COMPOSE() { docker compose -f "$REPO_DIR/docker-compose.yml" --project-directory "$REPO_DIR" "$@"; }
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

image_drift=0
if [ "$new" = "$last" ]; then
  # docker-ci image builds lag the merge by ~10-20min: master can be marked
  # "deployed" while :latest still carries the previous commit's code
  # (2026-11-23 — the funnel-sender merge sat undeployed because last_sha was
  # already written). Probe the two backend tags; if a pull moves the local
  # image id, fall through and recreate on the fresh image.
  for img in social-backend-latest social-backend-api-latest; do
    ref="ghcr.io/themis128/cu130-slim:$img"
    before=$(docker image inspect "$ref" --format '{{.Id}}' 2>/dev/null || echo none)
    docker pull -q "$ref" >/dev/null 2>&1 || continue
    after=$(docker image inspect "$ref" --format '{{.Id}}' 2>/dev/null || echo none)
    [ "$before" != "$after" ] && image_drift=1
  done
  [ "$image_drift" = 0 ] && exit 0
  log "master unchanged ($new) but backend :latest image drifted — redeploying"
fi

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
[ "$image_drift" = 1 ] && app_code=1
case "$changed" in
  *social-automation/backend/app/*|*social-automation/backend/alembic/*|*social-automation/backend/scripts/*) app_code=1 ;;
esac
case "$changed" in
  *docker-compose*.y*ml|*compose*.y*ml|*.env*|Dockerfile*) compose_change=1 ;;
esac

publish_busy() {
  # Recreating a publishing worker mid-publish kills the post (2026-11-21:
  # a LinkedIn publish died 3s after a force-recreate). Defer recreations
  # while a publishing task is active; deploy-watch retries next cycle
  # (max 10 min delay). Celery inspect failure counts as NOT busy so a
  # dead worker can never block deploys.
  # Meta-lane publishes (facebook/instagram/threads) run on
  # social-worker-meta, not social-worker-publishing — check both or a
  # Meta publish dies exactly like the LinkedIn one did.
  # social-worker-media: a video render is 10-40min of GPU — killing it
  # mid-flight orphans the ComfyUI job (the prompt_id resume checkpoint
  # softens this, but a kill during expand/QA still restarts the render).
  # Defer while one is active; DW_MAX_DEFERS caps the delay so a render
  # backlog can't freeze deploys.
  local w act
  for w in social-worker-publishing social-worker-meta; do
    act=$(COMPOSE exec -T "$w" celery -A app.worker.celery_app inspect active --timeout=8 2>/dev/null || true)
    printf '%s' "$act" | grep -q "app.worker.tasks.publishing" && return 0
  done
  act=$(COMPOSE exec -T social-worker-media celery -A app.worker.celery_app inspect active --timeout=8 2>/dev/null || true)
  printf '%s' "$act" | grep -q "generate_video_asset_task" && return 0
  return 1
}

defer_file="$DW_STATE_DIR/defer-$new"
if { [ "$compose_change" = 1 ] || [ "$app_code" = 1 ]; } && publish_busy; then
  # Exit BEFORE the deployed-sha state write below — a deferred cycle must
  # leave last_sha untouched so the next run retries the deploy. force-
  # recreate kills publishing AND media workers, so this gate sits above
  # the compose-up/recreate blocks, not just the rebuild path.
  defers=$(( $(cat "$defer_file" 2>/dev/null || echo 0) + 1 ))
  if [ "$defers" -lt "$DW_MAX_DEFERS" ]; then
    echo "$defers" > "$defer_file"
    log "work in flight (publish or video render) — deferring deploy ($defers/$DW_MAX_DEFERS, sha stays pending)"
    exit 0
  fi
  log "defer cap hit ($DW_MAX_DEFERS) — deploying despite in-flight work"
fi

if [ "$compose_change" = 1 ]; then
  log "compose/env files changed — compose up -d (root-owned services excluded)"
  UP_SERVICES=$(COMPOSE config --services | grep -vxFf <(printf '%s\n' $ROOT_OWNED) | tr '\n' ' ')
  COMPOSE up -d $UP_SERVICES >>"$DW_LOG" 2>&1 || ALERT "compose-up" ":rotating_light: master-deploy-watch: compose up -d failed after $new"
fi
if [ "$app_code" = 1 ]; then
  # The backend runs GHCR images (social-backend-api-latest /
  # social-backend-latest): `compose restart` bounces the STALE baked image
  # and never deploys merged code - deployed code changes land via registry
  # pulls, not restarts. Pull the fresh tags and force-recreate.
  log "backend code changed — pull + force-recreate $CORE_RESTART"
  COMPOSE pull $CORE_RESTART >>"$DW_LOG" 2>&1 || ALERT "pull" ":rotating_light: master-deploy-watch: compose pull failed after $new"
  COMPOSE up -d --force-recreate $CORE_STANDALONE >>"$DW_LOG" 2>&1 || ALERT "recreate" ":rotating_light: master-deploy-watch: compose up --force-recreate failed after $new"
  ROOT_COMPOSE up -d --no-deps --pull never --force-recreate $CORE_ROOT >>"$DW_LOG" 2>&1 || ALERT "recreate-root" ":rotating_light: master-deploy-watch: root compose force-recreate failed after $new"
fi

# ── local-image services: rebuild when files under their build context
# changed. These services have no GHCR image — `compose up -d` alone would
# keep running the stale baked image (e.g. linkedin-browser-sidecar/server.js).
# Watchtower can't help here: it only pulls registry tags, never builds.
# State (/data: session + rate-limit.json) lives on a named volume, so a
# rebuild+recreate preserves the LinkedIn cooldown and login session.
LOCAL_IMAGE_CTX=(
  "linkedin-browser-sidecar/:linkedin-browser-sidecar"
  "tiktok-browser-sidecar/:tiktok-browser-sidecar"
  "messenger-sidecar/:messenger-sidecar"
  "facebook-browser-sidecar/:facebook-browser-sidecar"
  "browser-novnc/:browser-novnc engagement-browser"
  "instagram-private-api/:instagram-private-api"
  "airbyte-mcp-server/:airbyte-mcp-server"
  "stack-ops/:stack-ops"
  "monitoring/promtail/:promtail"
)
rebuild_svcs=""
while IFS= read -r f; do
  [ -z "$f" ] && continue
  for pair in "${LOCAL_IMAGE_CTX[@]}"; do
    ctx="${pair%%:*}"; svcs="${pair#*:}"
    case "$f" in
      "$ctx"*)
        for s in $svcs; do
          case " $rebuild_svcs " in *" $s "*) ;; *) rebuild_svcs="$rebuild_svcs $s" ;; esac
        done ;;
    esac
  done
done <<< "$changed"
rebuild_svcs="${rebuild_svcs# }"
if { [ "$compose_change" = 1 ] || [ "$app_code" = 1 ] || [ -n "$rebuild_svcs" ]; } && publish_busy; then
  # Second check for work that started between the main gate and here —
  # the rebuild path recreates publish-lane sidecars too.
  defers=$(( $(cat "$defer_file" 2>/dev/null || echo 0) + 1 ))
  if [ "$defers" -lt "$DW_MAX_DEFERS" ]; then
    echo "$defers" > "$defer_file"
    log "work in flight — deferring deploy ($defers/$DW_MAX_DEFERS, sha stays pending)"
    exit 0
  fi
  log "defer cap hit ($DW_MAX_DEFERS) — deploying despite in-flight work"
fi
if [ -n "$rebuild_svcs" ]; then
  log "build-context changed — rebuilding:$rebuild_svcs"
  # shellcheck disable=SC2086
  if COMPOSE build $rebuild_svcs >>"$DW_LOG" 2>&1 && \
     COMPOSE up -d $rebuild_svcs >>"$DW_LOG" 2>&1; then
    log "rebuilt+recreated:$rebuild_svcs"
    # Each compose build orphans the previous image; merge storms pile up
    # ~50GB of dead layers per day inside docker_data.vhdx. Cap it: prune
    # dangling images after every rebuild cycle (dangling only - tagged
    # and in-use images are untouched).
    docker image prune -f >>"$DW_LOG" 2>&1 || true
  else
    ALERT "image-rebuild" ":rotating_light: master-deploy-watch: docker compose build/up failed for:$rebuild_svcs — check $DW_LOG"
  fi
fi

# ── health gate: api + all 4 celery nodes ─────────────────────────────
sleep 25
fails_file="$DW_STATE_DIR/fails-$new"
fails=0; [ -f "$fails_file" ] && fails=$(cat "$fails_file")
healthy=1
curl -sf --max-time 10 "$HEALTH_URL" >/dev/null || healthy=0
nodes=$(COMPOSE exec -T social-worker-publishing celery -A app.worker.celery_app inspect ping 2>/dev/null | grep -c "pong" || true)
[ "${nodes:-0}" -lt 4 ] && healthy=0

if [ "$healthy" = 1 ]; then
  echo "$new" > "$DW_STATE_DIR/last_sha"
  rm -f "$fails_file" "$DW_STATE_DIR"/defer-*
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
