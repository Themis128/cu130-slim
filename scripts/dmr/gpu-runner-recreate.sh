#!/usr/bin/env bash
# gpu-runner-recreate.sh — restore/self-heal the GPU Docker Model Runner.
#
# The `docker-model-runner` container serves the app on host port 12435:
#   - llama.cpp (CUDA, -ngl 999 full GPU offload) for GGUF models — production
#   - vLLM 0.27.1 for safetensors models — experimental
#
# It normally has RestartPolicy=always and persists models in the
# `docker-model-runner-models` named volume. This script handles the cases
# that policy misses:
#   1. Container exists but stopped/wedged  -> start (or restart if engine dead)
#   2. Container missing (Desktop reset)    -> recreate from the fixed image
#
# The fixed image `local/model-runner:vllm-cuda-fixed` is a commit of the
# stock `docker/model-runner:latest-vllm-cuda` with torch 2.13.0+cu126
# installed into /opt/vllm-env (the stock image ships a broken +cpu torch).
#
# Usage:
#   ./gpu-runner-recreate.sh          # heal or recreate
#   ./gpu-runner-recreate.sh --force  # always recreate the container
#
# After recreation, re-apply per-model vLLM memory config:
#   docker model configure --gpu-memory-utilization 0.7 ai/smollm2-vllm

set -euo pipefail

NAME="docker-model-runner"
IMAGE="local/model-runner:vllm-cuda-fixed"
PORT="12435"
MODELS_VOLUME="docker-model-runner-models"
HEALTH_URL="http://localhost:${PORT}/engines/v1/models"

# WSL2 NVIDIA userspace libs mounted by Docker Desktop (host-specific path —
# if this path is absent the GPU layers will not work; re-derive with:
#   docker inspect docker-model-runner --format '{{json .HostConfig.Binds}}')
WSL_LIB_SRC="/run/desktop/mnt/host/wsl/docker-desktop-bind-mounts/Ubuntu-26.04/eead6ba894879fdc294fc12b89c87a9375c56ccfe49d00db241cbfecbdbf5a1a"

log() { echo "[gpu-runner] $*"; }

healthy() { curl -sf -m 5 "$HEALTH_URL" >/dev/null 2>&1; }

if [ "${1:-}" = "--force" ]; then
    log "--force: removing existing container"
    docker rm -f "$NAME" >/dev/null 2>&1 || true
fi

if docker inspect "$NAME" >/dev/null 2>&1; then
    if healthy; then
        log "runner is up and healthy on :${PORT} — nothing to do"
        exit 0
    fi
    log "container exists but engine unhealthy — restarting"
    docker restart "$NAME"
    sleep 5
    if healthy; then
        log "runner recovered after restart"
        exit 0
    fi
    log "restart did not recover — recreating container"
    docker rm -f "$NAME" >/dev/null 2>&1 || true
fi

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    log "ERROR: $IMAGE not found locally."
    log "Rebuild it: docker model install-runner --backend vllm --gpu cuda --port ${PORT} \\"
    log "  then fix torch inside the container:"
    log "    /home/modelrunner/.local/bin/uv pip install --python /opt/vllm-env/bin/python \\"
    log "      --index-url https://download.pytorch.org/whl/cu126 --force-reinstall 'torch==2.13.0'"
    log "  and commit: docker commit <container> ${IMAGE}"
    exit 1
fi

WSL_MOUNT_ARGS=()
if [ -d "$WSL_LIB_SRC" ]; then
    WSL_MOUNT_ARGS=(-v "${WSL_LIB_SRC}:/usr/lib/wsl/lib:ro")
else
    log "WARNING: WSL lib dir $WSL_LIB_SRC not found — GPU may not initialize"
fi

log "creating $NAME from $IMAGE on 127.0.0.1:${PORT}"
docker run -d \
    --name "$NAME" \
    --restart always \
    --gpus all \
    -p "127.0.0.1:${PORT}:12435" \
    -v "${MODELS_VOLUME}:/models:z" \
    "${WSL_MOUNT_ARGS[@]}" \
    -e MODEL_RUNNER_PORT=12435 \
    -e MODEL_RUNNER_ENVIRONMENT=moby \
    -e MODEL_RUNNER_SOCK=/var/run/model-runner/model-runner.sock \
    -e MODELS_PATH=/models \
    -e LLAMA_ARG_HOST=0.0.0.0 \
    -e VLLM_WSL2_ENABLE_PIN_MEMORY=1 \
    -e VLLM_USE_FLASHINFER_SAMPLER=0 \
    "$IMAGE"

log "waiting for engine health..."
for i in $(seq 1 24); do
    if healthy; then
        log "runner healthy on :${PORT} (${i}0s)"
        exit 0
    fi
    sleep 10
done

log "ERROR: runner did not become healthy within 240s — check: docker logs $NAME"
exit 1
