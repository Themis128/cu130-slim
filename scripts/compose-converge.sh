#!/usr/bin/env bash
# compose-converge.sh — bounded replacement for compose-healer's whole-project
# `up -d` loop (which OOM-killed the stack mid-recreate twice on 2026-11-04).
# Only restarts services that are Exited/Created from a CORE allowlist —
# never recreates healthy containers, never pulls.
set -u
CORE="social-api social-api-gateway social-worker-default social-worker-publishing social-worker-media social-worker-messenger celery-beat social-postgres redis social-cloudflared social-frontend"
cd "$(dirname "$0")/.."

# Deploy lock — another agent/human is deploying; hands off entirely
# (same contract as compose-watchdog.sh). Fresh = DEPLOY_LOCK_MAX_AGE secs.
DEPLOY_LOCK="${DEPLOY_LOCK:-.deploy.lock}"
DEPLOY_LOCK_MAX_AGE="${DEPLOY_LOCK_MAX_AGE:-3600}"
if [ -f "$DEPLOY_LOCK" ]; then
  age=$(( $(date +%s) - $(date -r "$DEPLOY_LOCK" +%s) ))
  if [ "$age" -lt "$DEPLOY_LOCK_MAX_AGE" ]; then
    echo "converge: deploy lock fresh (${age}s) — skipping"
    exit 0
  fi
fi

# fsbridge probe — when \wsl.localhost bind mounts are poisoned, every
# docker start we issue just produces another exit-127. Probe ONE real
# source file; if it can't be read back through a mount, skip the pass.
if ! docker run --rm -v "$PWD/scripts/compose-converge.sh:/converge.probe:ro"     docker:27-cli sh -c 'test -s /converge.probe' >/dev/null 2>&1; then
  echo "converge: fsbridge mounts poisoned — skipping (heal via wsl --shutdown)"
  exit 0
fi

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
