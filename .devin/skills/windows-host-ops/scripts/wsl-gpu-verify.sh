#!/usr/bin/env bash
# Post-WSL-bounce GPU verification + ComfyUI rebind.
# Run inside Ubuntu-26.04 after `wsl --shutdown` (wsl-gpu-rebind.ps1 calls this).
#
# Signature being repaired: `nvidia-smi` -> "GPU access blocked by the
# operating system" and dmesg `dxgvmb_send_create_process failed -75`,
# caused by an NVIDIA driver install/hot-swap while the WSL VM was running.
# The VM's dxg channel stays bound to the pre-install driver instance until
# the VM is shut down.

set -u
cd /home/tbaltzakis/cu130-slim || exit 1

echo "[gpu-verify] $(date -Is) starting"

# 1. Wait for Docker (Docker Desktop re-runs its WSL backend after a bounce).
for i in $(seq 1 60); do
  docker info >/dev/null 2>&1 && break
  sleep 5
done
docker info >/dev/null 2>&1 || { echo "[gpu-verify] docker never came up"; exit 1; }

# 2. Host-injected GPU check.
if nvidia-smi >/dev/null 2>&1; then
  echo "[gpu-verify] nvidia-smi OK:"
  nvidia-smi --query-gpu=name,driver_version,memory.used,memory.total \
    --format=csv,noheader
else
  echo "[gpu-verify] nvidia-smi STILL BLOCKED — check Windows driver state"
  exit 2
fi

# 3. Container GPU check. A container created while GPU-P was down lacks the
#    dxg mapping, so force-recreate if torch still sees CPU only.
for svc in comfyui; do
  cid="social-media-comfyui-gpu"
  docker inspect "$cid" >/dev/null 2>&1 || { echo "[gpu-verify] $cid absent, creating"; docker compose up -d "$svc"; sleep 20; }

  for i in 1 2 3; do
    cuda=$(docker exec "$cid" sh -c \
      'python3 -c "import torch; print(torch.cuda.is_available())"' 2>/dev/null)
    [ "$cuda" = "True" ] && break
    sleep 10
  done

  if [ "$cuda" = "True" ]; then
    echo "[gpu-verify] $svc cuda OK"
  else
    echo "[gpu-verify] $svc cuda still False — force-recreating"
    docker compose up -d --force-recreate "$svc"
    sleep 30
    cuda=$(docker exec "$cid" sh -c \
      'python3 -c "import torch; print(torch.cuda.is_available())"' 2>/dev/null)
    echo "[gpu-verify] $svc cuda after recreate: ${cuda:-unknown}"
  fi
done

echo "[gpu-verify] done"
