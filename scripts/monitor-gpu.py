#!/usr/bin/env python3
"""Monitor GPU while ComfyUI (or any local GPU workload) is running.
Logs nvidia-smi metrics to ./logs/gpu-monitor-YYYY-MM-DD-HHMMSS.csv.
Usage:
  monitor-gpu.py                  # run until Ctrl-C
  monitor-gpu.py 600              # run for 10 minutes
  monitor-gpu.py 600 5            # sample every 5 seconds
  monitor-gpu.py 600 5 /path/log  # custom log path"""

import csv
import io
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
duration = int(sys.argv[1]) if len(sys.argv) > 1 else 0   # 0 = infinite
interval = int(sys.argv[2]) if len(sys.argv) > 2 else 5
log_file = Path(sys.argv[3]) if len(sys.argv) > 3 else \
    ROOT / f"logs/gpu-monitor-{datetime.now():%Y%m%d-%H%M%S}.csv"
log_file.parent.mkdir(parents=True, exist_ok=True)

if not shutil.which("nvidia-smi"):
    print("nvidia-smi not found in PATH. If running inside a container, "
          "run this on the WSL host.", file=sys.stderr)
    sys.exit(1)

GPU_FIELDS = ("timestamp,index,name,pci.bus_id,utilization.gpu,"
              "utilization.memory,memory.used,memory.total,"
              "temperature.gpu,power.draw,power.limit")

with open(log_file, "w", newline="") as f:
    f.write("timestamp,gpu_index,gpu_name,pci_bus_id,util_gpu_pct,"
            "util_mem_pct,memory_used_mb,memory_total_mb,temperature_c,"
            "power_draw_w,power_limit_w,processes_json\n")

print(f"Logging GPU metrics to {log_file} "
      f"(interval={interval}s, duration={duration}s)")
print("Press Ctrl-C to stop.")

start = time.time()
try:
    while True:
        if duration > 0 and time.time() - start >= duration:
            print("Duration reached. Stopping.")
            break

        r = subprocess.run(
            ["nvidia-smi", f"--query-gpu={GPU_FIELDS}",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True)
        lines = r.stdout.splitlines()

        with open(log_file, "a", newline="") as f:
            for line in lines:
                idx = line.split(",")[1].strip() if "," in line else "0"
                pr = subprocess.run(
                    ["nvidia-smi", "--query-compute-apps=pid,name,used_memory",
                     "--format=csv,noheader,nounits", "-i", idx],
                    capture_output=True, text=True)
                rows = []
                for pline in pr.stdout.splitlines():
                    parts = [p.strip() for p in pline.split(",")]
                    if len(parts) >= 3 and "None" not in pline:
                        rows.append({"pid": parts[0], "name": parts[1],
                                     "mem_mb": parts[2]})
                f.write(f"{line},{json.dumps(rows)}\n")

        time.sleep(interval)
except KeyboardInterrupt:
    pass
