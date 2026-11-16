#!/usr/bin/env bash
# run-backend-once.sh — launch a throwaway SocialAuto backend task container
# with FRESH credentials, in both fsbridge states.
#
# Why this exists (2026-11-16): strategy-report-run3 ran with a stale
# REDIS_URL baked into Config.Env — pydantic env vars beat the mounted
# /app/.env, so the report degraded all run (Redis auth fails, GPU
# arbiter unserialized). And once the fsbridge poisons, ANY new
# \\wsl.localhost bind mount materializes empty — so the launcher must
# not blindly mount code.
#
# Usage:
#   scripts/run-backend-once.sh <name> '<python/celery command>' [--mounted]
#
#   <name>   container name (letters/digits/-/_)
#   command  e.g. "python3 -u -c 'from app.worker.tasks.digest import ...'"
#   --mounted  force bind-mounted code (only when fsbridge probe is clean)
#
# Default path = baked image + .env docker-cp'd in (works ALWAYS, code may
# lag master). --mounted = dev-latest code via bind mounts (requires a
# healthy fsbridge — the script probes first and refuses if poisoned).
set -euo pipefail
cd "$(dirname "$0")/.."

NAME="${1:?usage: run-backend-once.sh <name> '<cmd>' [--mounted]}"
CMD="${2:?missing command}"
MODE="${3:-baked}"
IMAGE="ghcr.io/themis128/cu130-slim:social-backend-latest"
NETS="cu130-backend cu130-db"

probe_ok() {
  # definitive test: can a fresh container read a WSL-sourced bind mount?
  docker run --rm -v "$PWD/AGENTS.md:/p:ro" alpine cat /p >/dev/null 2>&1
}

if [ "$MODE" = "--mounted" ]; then
  if ! probe_ok; then
    echo "fsbridge poisoned — refusing bind mounts; drop --mounted" >&2
    exit 1
  fi
  echo "launching $NAME (mounted code, live .env)"
  exec docker run -d --name "$NAME" \
    --network cu130-backend --network cu130-db \
    --env-file "$PWD/.env" \
    -v "$PWD/.env:/app/.env:ro" \
    -v "$PWD/notebooks:/notebooks" \
    -v "$PWD/social-automation/backend/app:/app/app" \
    -v "$PWD/social-automation/backend/scripts:/app/scripts" \
    "$IMAGE" sh -c "$CMD"
fi

# baked path: fresh env copied INTO the container fs (no bind mounts at all)
echo "launching $NAME (baked image + fresh .env)"
docker create --name "$NAME" \
  $(for n in $NETS; do printf -- '--network %s ' "$n"; done) \
  "$IMAGE" sh -c "$CMD" >/dev/null
docker cp "$PWD/.env" "$NAME:/app/.env"
docker start "$NAME" >/dev/null
echo "started $NAME — logs: docker logs -f $NAME"
