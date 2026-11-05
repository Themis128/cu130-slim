#!/usr/bin/env bash
# safe-recreate.sh <service...> — recreate compose services without the
# docker-desktop-bind-mounts failure mode:
#   1. probe-mounts — a throwaway container mounting the same ./.env path
#      re-prepares the hashed bind-mount on the VM side (the recurring
#      "mount src=...54175c...: no such file or directory" failure)
#   2. compose up -d --pull never (credential helper is dead under broken
#      interop — never pull)
#   3. docker start fallback for any service still Exited
# Usage: scripts/safe-recreate.sh social-api [more services]
set -u
cd "$(dirname "$0")/.."
IMG=$(docker compose images -q "$1" 2>/dev/null | head -1)
[ -z "$IMG" ] && IMG=$(docker images --format '{{.Repository}}:{{.Tag}}' | grep social-backend-api | head -1)
echo "probe image: $IMG"
docker run --rm --pull never -v "$PWD/.env:/app/.env:ro" "$IMG" sh -c 'test -f /app/.env' \
  && echo "env mount probe: ok"
docker compose up -d --pull never "$@" 2>&1 | tail -5
sleep 8
for s in "$@"; do
  st=$(docker inspect -f '{{.State.Status}}' "$s" 2>/dev/null || echo missing)
  [ "$st" = "running" ] || { echo "$s: $st -> docker start"; docker start "$s" >/dev/null && echo "$s started"; }
done
docker compose ps --format '{{.Name}} {{.Status}}' | grep -E "$(IFS='|'; echo "$*")"
