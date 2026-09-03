#!/usr/bin/env bash
# Check DMR status: loaded models, VRAM, health
set -euo pipefail

echo "=== Docker Model Runner Status ==="
echo ""

# Check if DMR is reachable
if curl -sf http://localhost:12434/engines/v1/models >/dev/null 2>&1; then
    echo "  DMR API:      ONLINE (http://localhost:12434)"
else
    echo "  DMR API:      OFFLINE"
    exit 1
fi

echo ""
echo "=== Loaded Models ==="
curl -sf http://localhost:12434/engines/v1/models 2>/dev/null | python3 -c '
import sys, json
d = json.load(sys.stdin)
models = d.get("data", [])
if not models:
    print("  (no models loaded — they load on first request)")
for m in models:
    print("  %s" % m.get("id", "?"))
'

echo ""
echo "=== Local Models (pulled) ==="
docker model list 2>/dev/null || echo "  (docker model list failed)"

echo ""
echo "=== GPU VRAM ==="
if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi --query-gpu=memory.used,memory.free,memory.total --format=csv,noheader 2>/dev/null | while IFS=, read -r used free total; do
        echo "  Used: ${used}  Free: ${free}  Total: ${total}"
    done
else
    echo "  (nvidia-smi not available)"
fi

echo ""
echo "=== DMR Disk Usage ==="
docker model df 2>/dev/null || echo "  (docker model df failed)"
