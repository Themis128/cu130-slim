#!/bin/bash
set -e

# libcuda.so for triton JIT compilation. WSL ships only libcuda.so.1.1 under
# the per-container driver dir, and that mount is populated lazily — so poll
# briefly, then ALSO drop a container-local copy on the linker path (the
# drivers dir can be individually file-mounted read-only, and symlinks
# created inside one container instance do not survive a restart).
CUDA_READY=0
for _ in $(seq 1 60); do
  for d in /usr/lib/wsl/drivers/nvltsi.inf_amd64_*/; do
    if [ -f "$d/libcuda.so.1.1" ]; then
      ln -sf libcuda.so.1.1 "$d/libcuda.so" 2>/dev/null || true
      if cp -L "$d/libcuda.so.1.1" /usr/lib/x86_64-linux-gnu/libcuda.so 2>/dev/null; then
        echo "libcuda.so staged into /usr/lib/x86_64-linux-gnu ($d)"
      fi
      CUDA_READY=1
      break
    fi
  done
  [ "$CUDA_READY" = 1 ] && break
  sleep 1
done
[ "$CUDA_READY" = 0 ] && echo "WARN: WSL driver libs not populated; triton JIT may fail"

mkdir -p /home/user/ComfyUI/{custom_nodes,models,output,input}
ln -sfn /home/user/ComfyUI/models /opt/ComfyUI/models
ln -sfn /home/user/ComfyUI/output /opt/ComfyUI/output
ln -sfn /home/user/ComfyUI/input /opt/ComfyUI/input
ln -sfn /home/user/ComfyUI/custom_nodes /opt/ComfyUI/custom_nodes

PROFILE=${COMFYUI_PROFILE:-sdxl}
PROFILE_FILE="/opt/ComfyUI/profiles/${PROFILE}.txt"
PROFILE_ARGS=""
if [ -f "$PROFILE_FILE" ]; then
  PROFILE_ARGS=$(grep -v "^#" "$PROFILE_FILE" | tr "\n" " ")
fi

BASE_ARGS="--listen 0.0.0.0 --port 8000 --preview-method auto"
exec python3 main.py $BASE_ARGS $PROFILE_ARGS ${CLI_ARGS:-}
