#!/usr/bin/env bash
# Generate embeddings via DMR
# Usage: dmr-embed.sh "text to embed" [model]
# Default model: ai/qwen3-embedding
set -euo pipefail

TEXT="${1:?Usage: dmr-embed.sh \"text to embed\" [model]}"
MODEL="${2:-ai/qwen3-embedding}"

echo "Model: $MODEL"
echo "Input: $TEXT"
echo ""

curl -s http://localhost:12434/engines/llama.cpp/v1/embeddings \
  -H "Content-Type: application/json" \
  -d "$(python3 -c "
import json
print(json.dumps({
    'model': '$MODEL',
    'input': '''$TEXT''',
}))
")" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
    emb = d["data"][0]["embedding"]
    print("Dimensions: %d" % len(emb))
    print("First 5: %s" % emb[:5])
    print("Model: %s" % d.get("model", "?"))
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
'
