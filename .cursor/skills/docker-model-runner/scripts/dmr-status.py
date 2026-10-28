#!/usr/bin/env python3
"""Check DMR status: loaded models, VRAM, health."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import dmr_get, dmr_online, docker_model  # noqa: E402

print("=== Docker Model Runner Status ===\n")
if dmr_online():
    print("  DMR API:      ONLINE (http://localhost:12435)")
elif subprocess.run(["docker", "model", "status"],
                    capture_output=True).returncode == 0:
    print("  DMR API:      OFFLINE (TCP), CLI: ONLINE")
else:
    print("  DMR API:      OFFLINE")
    sys.exit(1)

print("\n=== Loaded Models ===")
raw = dmr_get("/engines/v1/models")
if raw is not None:
    models = json.loads(raw).get("data", [])
    if not models:
        print("  (no models loaded — they load on first request)")
    for m in models:
        print(f'  {m.get("id", "?")}')
else:
    docker_model("ps")

print("\n=== Local Models (pulled) ===")
docker_model("list")

print("\n=== GPU VRAM ===")
if shutil.which("nvidia-smi"):
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used,memory.free,memory.total",
         "--format=csv,noheader"], capture_output=True, text=True).stdout.strip()
    for line in out.splitlines():
        u, f, t = [x.strip() for x in line.split(",")]
        print(f"  Used: {u}  Free: {f}  Total: {t}")
else:
    print("  (nvidia-smi not available)")

print("\n=== DMR Disk Usage ===")
docker_model("df")
