#!/usr/bin/env bash
# Show GPU VRAM + utilization (RTX 3070 8GB budget check)
set -euo pipefail
if ! command -v nvidia-smi >/dev/null 2>&1; then
    echo "nvidia-smi not available"; exit 1
fi
nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.free,memory.total,power.draw \
  --format=csv,noheader,nounits | while IFS=, read -r name util used free total power; do
    echo "GPU:         $name"
    echo "VRAM:        ${used} MiB used / ${free} MiB free / ${total} MiB total"
    echo "Utilization: ${util}%   Power: ${power} W"
    echo ""
    echo "Budget guide: 4B pinned ~2.7GB + 8B ~5.1GB = ~7.8GB worst case."
    echo "Vision (~5GB) and embeddings (~1GB) evict the unpinned 8B as needed."
done
