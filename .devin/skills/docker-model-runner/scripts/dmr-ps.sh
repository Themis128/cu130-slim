#!/usr/bin/env bash
# List running (loaded in memory) DMR models
# Usage: dmr-ps.sh
set -euo pipefail

echo "=== Running Models (loaded in memory) ==="

# Try API first
if curl -sf http://localhost:12434/inference/ps >/dev/null 2>&1; then
    curl -sf http://localhost:12434/inference/ps | python3 -c '
import sys, json
d = json.load(sys.stdin)
if not d:
    print("  (no models loaded)")
for item in d if isinstance(d, list) else [d]:
    backend = item.get("backend_name", "?")
    model = item.get("model_name", "?")
    mode = item.get("mode", "?")
    in_use = item.get("in_use", False)
    loading = item.get("loading", False)
    last = item.get("last_used", "?")
    status = "IN USE" if in_use else ("LOADING" if loading else "IDLE")
    print(f"  {model} [{backend}] mode={mode} status={status} last_used={last}")
'
else
    # CLI fallback
    docker model ps 2>/dev/null || echo "  (no models loaded or DMR offline)"
fi
