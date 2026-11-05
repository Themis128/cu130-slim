#!/usr/bin/env bash
# compose-converge.sh — bounded replacement for compose-healer's whole-project
# `up -d` loop (which OOM-killed the stack mid-recreate twice on 2026-11-04).
# Only restarts services that are Exited/Created from a CORE allowlist —
# never recreates healthy containers, never pulls.
set -u
CORE="social-api social-api-gateway social-worker-default social-worker-publishing social-worker-media social-worker-messenger celery-beat social-postgres redis social-cloudflared social-frontend"
cd "$(dirname "$0")/.."
changed=0
for s in $CORE; do
  st=$(docker inspect -f '{{.State.Status}}' "$s" 2>/dev/null || echo missing)
  case "$st" in
    exited|created|dead)
      echo "converge: $s is $st -> docker start"
      docker start "$s" >/dev/null 2>&1 && changed=$((changed+1)) || echo "  start failed: $s"
      ;;
  esac
done
echo "converged: restarted $changed"
