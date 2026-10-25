#!/usr/bin/env bash
# x-status.sh — verify the X token is healthy post-reauth.
# Checks recent worker logs for refresh-400s; clean = reconnected.
cd "$(dirname "$0")/../../.." || exit 1
docker compose logs --since 24h social-worker-publishing 2>&1 | grep -c "twitter.*400\|refresh.*failed" | xargs -I{} sh -c 'echo "refresh-400s in 24h: {}"; test {} -eq 0'
