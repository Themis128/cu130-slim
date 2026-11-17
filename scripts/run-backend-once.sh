#!/usr/bin/env bash
# run-backend-once.sh — launch a throwaway SocialAuto backend task container
# with FRESH credentials, in both fsbridge states.
#
# Why this exists (2026-11-16): strategy-report-run3 ran with a stale
# REDIS_URL baked into Config.Env — pydantic env vars beat the mounted
# /app/.env, so the report degraded all run (Redis auth fails, GPU
# arbiter unserialized), then WEDGED in a Celery result-store retry loop.
# And once the fsbridge poisons, ANY new \\wsl.localhost bind mount
# materializes empty — so the launcher must not blindly mount code.
#
# SECOND failure class (same day): the on-disk .env is a PLACEHOLDER file
# (`REDIS_URL=redis://:redis_password@…`) — real secrets are injected by
# Infisical into the managed containers' env. Copying .env in therefore
# still yields a dead Redis. Fix: by default this script exports the
# live env from the social-api container and passes it via --env-file,
# so the throwaway always gets the same secrets the stack runs on.
#
# Usage:
#   scripts/run-backend-once.sh <name> '<python/celery command>' [--mounted|--bare]
#
#   <name>     container name (letters/digits/-/_)
#   <command>  e.g. "python3 -u -c 'from app.worker.tasks.digest import ...'"
#   --mounted  force bind-mounted code (only when fsbridge probe is clean)
#   --bare     skip live-secret injection (use on-disk .env only — for
#              tasks that don't touch Redis/DB/ComfyUI)
#
# Default path = baked image + live env (works ALWAYS, code may lag
# master). --mounted = dev-latest code via bind mounts (requires a
# healthy fsbridge — the script probes first and refuses if poisoned).
set -euo pipefail
cd "$(dirname "$0")/.."

NAME="${1:?usage: run-backend-once.sh <name> '<cmd>' [--mounted|--bare]}"
CMD="${2:?missing command}"
MODE="${3:-default}"
IMAGE="ghcr.io/themis128/cu130-slim:social-backend-latest"
NETS="cu130-backend cu130-db"
DONOR="${RUN_ONCE_DONOR:-social-api}"

TMPENV=""
cleanup() { [ -n "$TMPENV" ] && rm -f "$TMPENV"; }
trap cleanup EXIT

live_env() {
  # Dump the donor container's env as KEY=VALUE lines (no values printed
  # to the console). Falls back to on-disk .env if the donor is down.
  local f
  f=$(mktemp /tmp/run-once-env.XXXXXX)
  chmod 600 "$f"
  if docker exec "$DONOR" printenv > "$f" 2>/dev/null && [ -s "$f" ]; then
    # JWT_SECRET_KEY is read by pydantic from the mounted /app/.env, not the
    # process env — printenv misses it. Pull it from the donor's env file.
    if ! grep -q '^JWT_SECRET_KEY=' "$f"; then
      docker exec "$DONOR" sh -c 'grep "^JWT_SECRET_KEY=" /app/.env' >> "$f" 2>/dev/null || true
    fi
    echo "$f"
  elif [ -f "$PWD/.env" ]; then
    echo "warn: $DONOR unreachable — falling back to on-disk .env" >&2
    grep -vE '^\s*#|^\s*$' "$PWD/.env" > "$f"
    echo "$f"
  else
    echo "error: no env source (donor down, no .env)" >&2
    rm -f "$f"; return 1
  fi
}

probe_ok() {
  # definitive test: can a fresh container read a WSL-sourced bind mount?
  docker run --rm -v "$PWD/AGENTS.md:/p:ro" alpine cat /p >/dev/null 2>&1
}

if [ "$MODE" = "--mounted" ]; then
  if ! probe_ok; then
    echo "fsbridge poisoned — refusing bind mounts; drop --mounted" >&2
    exit 1
  fi
  TMPENV=$(live_env)
  echo "launching $NAME (mounted code, live env from $DONOR)"
  docker run -d --name "$NAME" \
    --network cu130-backend --network cu130-db \
    --env-file "$TMPENV" \
    -v "$PWD/notebooks:/notebooks" \
    -v "$PWD/social-automation/backend/app:/app/app" \
    -v "$PWD/social-automation/backend/scripts:/app/scripts" \
    "$IMAGE" sh -c "$CMD" >/dev/null
  echo "started $NAME (mounted code) — logs: docker logs -f $NAME"
  exit 0
fi

if [ "$MODE" != "--bare" ]; then
  TMPENV=$(live_env)
  echo "launching $NAME (baked image + live env from $DONOR)"
  docker create --name "$NAME" \
    $(for n in $NETS; do printf -- '--network %s ' "$n"; done) \
    --env-file "$TMPENV" \
    "$IMAGE" sh -c "$CMD" >/dev/null
  docker start "$NAME" >/dev/null
  echo "started $NAME — logs: docker logs -f $NAME"
  exit 0
fi

# --bare: on-disk .env only (no live-secret injection, no mounts)
echo "launching $NAME (baked image + on-disk .env — placeholders likely!)"
docker create --name "$NAME" \
  $(for n in $NETS; do printf -- '--network %s ' "$n"; done) \
  "$IMAGE" sh -c "$CMD" >/dev/null
docker cp "$PWD/.env" "$NAME:/app/.env"
docker start "$NAME" >/dev/null
echo "started $NAME — logs: docker logs -f $NAME"
