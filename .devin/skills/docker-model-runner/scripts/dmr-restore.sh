#!/usr/bin/env bash
# dmr-restore.sh — recover the Docker Model Runner after the
# docker-model-runner container is GONE (pruned / Desktop reset / failed
# update), not just unhealthy. dmr-watchdog only restarts an existing
# container — it cannot recreate one. Verified procedure 2026-11-15.
#
#   scripts/dmr-restore.sh           # full restore + verify
#   scripts/dmr-restore.sh --check   # report state only
set -euo pipefail

IMAGE="docker/model-runner:latest-vllm-cuda"
RUNNER="docker-model-runner"
PORT=12435
DOCKER="${DOCKER:-docker}"          # docker-engine-ops: pin docker.exe if bare docker hits the wrong daemon
DMR_CLI="${DMR_CLI:-docker-model}"   # cli-plugin binary name; falls back below

log() { printf '[dmr-restore] %s\n' "$*"; }

find_cli() {
  if command -v "$DMR_CLI" >/dev/null 2>&1; then echo "$DMR_CLI"; return; fi
  for p in /usr/local/lib/docker/cli-plugins/docker-model \
           /usr/libexec/docker/cli-plugins/docker-model \
           "$HOME/.docker/cli-plugins/docker-model"; do
    [ -x "$p" ] && { echo "$p"; return; }
  done
  if docker model version >/dev/null 2>&1; then echo "docker model"; return; fi
  return 1
}

runner_state() {
  $DOCKER ps -a --filter "name=^${RUNNER}$" --format '{{.Status}}' | head -1
}

api_ok() {
  curl -sf --max-time 5 "http://localhost:${PORT}/engines/v1/models" >/dev/null 2>&1 \
    || curl -sf --max-time 5 "http://host.docker.internal:${PORT}/engines/v1/models" >/dev/null 2>&1
}

if [ "${1:-}" = "--check" ]; then
  log "container: $(runner_state || echo MISSING)"
  log "image:     $(docker image inspect "$IMAGE" --format '{{.Id}}' 2>/dev/null || echo not-pulled)"
  log "cli:       $(find_cli || echo MISSING)"
  api_ok && log "api :${PORT}: UP" || log "api :${PORT}: DOWN"
  exit 0
fi

# 1. Already healthy?
if api_ok; then log "runner already serving on :${PORT} — nothing to do"; exit 0; fi

state="$($DOCKER ps -a --filter "name=^${RUNNER}$" --format '{{.Status}}' | head -1)"
if [ -n "$state" ]; then
  log "container exists ($state) but API is down — asking dmr-watchdog to handle it"
  log "(watchdog restarts after 3 failed ticks; if it stays dead >5min,"
  log " docker restart ${RUNNER} manually)"
  exit 2
fi

log "container ${RUNNER} is GONE — reinstalling"

# 2. Image (registry flakes with unexpected EOF — retry, layers cache)
if ! $DOCKER image inspect "$IMAGE" >/dev/null 2>&1; then
  for i in 1 2 3; do
    log "pulling $IMAGE (attempt $i)"
    $DOCKER pull "$IMAGE" && break || { log "pull failed — retrying"; sleep 10; }
  done
  $DOCKER image inspect "$IMAGE" >/dev/null || { log "FATAL: image pull failed"; exit 1; }
fi

# 3. CLI + install-runner (creates the managed container)
CLI="$(find_cli || true)"
if [ -z "$CLI" ]; then
  log "FATAL: no docker-model CLI — install Docker Desktop model-runner plugin first"
  exit 1
fi
log "using CLI: $CLI"
# Canonical reinstall (see SKILL.md "Runner container gone"): the env var
# skips the second port bind that wedges Docker Desktop's /forwards/expose.
# shellcheck disable=SC2086
_MODEL_RUNNER_TREAT_DESKTOP_AS_MOBY=1 $CLI install-runner \
  --backend vllm --gpu cuda --port "$PORT" || {
  log "FATAL: install-runner failed — if /forwards/expose 500, run"
  log "  net stop winnat && net start winnat (elevated PowerShell) then retry"
  exit 1
}

# 4. Wait for API
for i in $(seq 1 24); do
  api_ok && { log "API up on :${PORT} after ~$((i*5))s"; break; }
  sleep 5
done
api_ok || { log "FATAL: runner installed but API never came up — $DOCKER logs ${RUNNER}"; exit 1; }

# 5. Verify + hand off to watchdog for canonical configs
log "models endpoint: $(curl -s --max-time 5 http://localhost:${PORT}/engines/v1/models || true)"
log "loaded models:   $(curl -s --max-time 5 http://localhost:${PORT}/api/ps || true)"
log "NOTE: configure settings are in-memory — dmr-watchdog reapplies"
log "canonical configs within ~60s of detecting the new StartedAt."
log "If /api/ps stays empty after warm-up, models load on first request"
log "(or run scripts/dmr-warmup.py). Restore complete."
