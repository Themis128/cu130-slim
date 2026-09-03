#!/usr/bin/env bash
# Pull a new model to DMR
# Usage: dmr-pull.sh <model-name>
# Examples:
#   dmr-pull.sh ai/llama3.2
#   dmr-pull.sh ai/qwen2.5-coder
#   dmr-pull.sh hf.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF
set -euo pipefail

MODEL="${1:?Usage: dmr-pull.sh <model-name>}"

echo "Pulling model: $MODEL"
echo "This may take a while for large models..."
echo ""

docker model pull "$MODEL"

echo ""
echo "=== Model pulled successfully ==="
docker model inspect "$MODEL" 2>/dev/null | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
    tags = d.get("tags", [])
    fmt = d.get("config", {}).get("format", "?")
    print("  Tags: %s" % ", ".join(tags))
    print("  Format: %s" % fmt)
    print("  ID: %s" % d.get("id", "?")[:20])
except: pass
' 2>/dev/null || true
