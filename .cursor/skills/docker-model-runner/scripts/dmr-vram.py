#!/usr/bin/env python3
"""Show GPU VRAM + utilization (RTX 3070 8GB budget check)."""
import shutil
import subprocess
import sys

if not shutil.which("nvidia-smi"):
    print("nvidia-smi not available")
    sys.exit(1)

out = subprocess.run(
    ["nvidia-smi",
     "--query-gpu=name,utilization.gpu,memory.used,memory.free,memory.total,power.draw",
     "--format=csv,noheader,nounits"], capture_output=True, text=True).stdout.strip()
for line in out.splitlines():
    name, util, used, free, total, power = [x.strip() for x in line.split(",")]
    print(f"GPU:         {name}")
    print(f"VRAM:        {used} MiB used / {free} MiB free / {total} MiB total")
    print(f"Utilization: {util}%   Power: {power} W")
    print("\nBudget guide: 4B pinned ~2.7GB + 8B ~5.1GB = ~7.8GB worst case.")
    print("Vision (~5GB) and embeddings (~1GB) evict the unpinned 8B as needed.")
