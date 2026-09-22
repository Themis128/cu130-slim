#!/usr/bin/env bash
# Monitor GPU while ComfyUI (or any local GPU workload) is running.
# Logs nvidia-smi metrics to ./logs/gpu-monitor-YYYY-MM-DD-HHMMSS.csv.
# Usage:
#   ./scripts/monitor-gpu.sh                  # run until Ctrl-C
#   ./scripts/monitor-gpu.sh 600              # run for 10 minutes
#   ./scripts/monitor-gpu.sh 600 5            # sample every 5 seconds
#   ./scripts/monitor-gpu.sh 600 5 /path/log  # custom log path
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DURATION=${1:-0}      # 0 = infinite
INTERVAL=${2:-5}      # seconds between samples
LOG_FILE=${3:-"$ROOT/logs/gpu-monitor-$(date +%Y%m%d-%H%M%S).csv"}

mkdir -p "$(dirname "$LOG_FILE")"

if ! command -v nvidia-smi >/dev/null 2>&1; then
  echo "nvidia-smi not found in PATH. If running inside a container, run this on the WSL host." >&2
  exit 1
fi

# Header
cat > "$LOG_FILE" <<HEADER
timestamp,gpu_index,gpu_name,pci_bus_id,util_gpu_pct,util_mem_pct,memory_used_mb,memory_total_mb,temperature_c,power_draw_w,power_limit_w,processes_json
HEADER

echo "Logging GPU metrics to $LOG_FILE (interval=${INTERVAL}s, duration=${DURATION}s)"
echo "Press Ctrl-C to stop."

start=$(date +%s)
while true; do
  now=$(date +%s)
  if [ "$DURATION" -gt 0 ] && [ "$((now - start))" -ge "$DURATION" ]; then
    echo "Duration reached. Stopping."
    break
  fi

  # Per-GPU metrics + compact process list as JSON
  nvidia-smi --query-gpu=timestamp,index,name,pci.bus_id,utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu,power.draw,power.limit --format=csv,noheader,nounits 2>/dev/null | while IFS= read -r line; do
    # Build process JSON for this GPU index (extracted from line field 2)
    idx=$(echo "$line" | awk -F',' '{gsub(/ /,"",$2); print $2}')
    procs=$(nvidia-smi -i "$idx" --query-compute-apps=pid,process_name,used_memory --format=csv,noheader 2>/dev/null | python3 -c '
import sys, json
rows=[]
for line in sys.stdin:
    line=line.strip()
    if not line or "None" in line: continue
    parts=[p.strip() for p in line.split(",")]
    if len(parts)>=3:
        rows.append({"pid": parts[0], "name": parts[1], "mem_mb": parts[2]})
print(json.dumps(rows))
' 2>/dev/null || echo "[]")
    printf '%s,%s\n' "$line" "$procs" >> "$LOG_FILE"
  done

  sleep "$INTERVAL"
done
