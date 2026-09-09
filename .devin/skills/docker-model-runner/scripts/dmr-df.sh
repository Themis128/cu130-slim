#!/usr/bin/env bash
# Show DMR disk usage
# Usage: dmr-df.sh
set -euo pipefail

echo "=== Docker Model Runner Disk Usage ==="

# Try API first
if curl -sf http://localhost:12434/inference/df >/dev/null 2>&1; then
    curl -sf http://localhost:12434/inference/df | python3 -m json.tool
else
    # CLI fallback
    docker model df 2>/dev/null || echo "  (DMR offline or df failed)"
fi
