#!/usr/bin/env bash
# wsl-checkout-sync-check.sh — detect WSL↔Windows checkout drift.
# Docker Desktop mounts C:\Users\tbaltzakis\cu130-slim (the WINDOWS copy),
# NOT /home/tbaltzakis/cu130-slim (the WSL copy where agents work).
# Drift in bind-mounted files = containers run stale code — this was the
# compose-healer crash-loop root cause (fixed YAML lived only in WSL).
# Usage: wsl-checkout-sync-check.sh [--sync]   (--sync copies WSL→Windows)
set -u
WSL=/home/tbaltzakis/cu130-slim
WIN=/mnt/c/Users/tbaltzakis/cu130-slim
[ -d "$WIN" ] || { echo "windows checkout unreachable"; exit 1; }
drift=0
for f in docker-compose.yml .env \
         scripts/compose-converge.sh scripts/safe-recreate.sh \
         social-automation/backend/app/api/accounts.py \
         social-automation/backend/app/services/linkedin_api.py \
         social-automation/backend/app/worker/tasks/consent_healers.py \
         social-automation/backend/scripts/linkedin_scope_audit.py; do
  [ -f "$WSL/$f" ] || continue
  if ! cmp -s "$WSL/$f" "$WIN/$f" 2>/dev/null; then
    echo "DRIFT  $f"
    drift=$((drift+1))
    [ "${1:-}" = "--sync" ] && cp "$WSL/$f" "$WIN/$f" && echo "  -> synced"
  fi
done
[ "$drift" -eq 0 ] && echo "clean: WSL and Windows checkouts in sync"
