#!/usr/bin/env bash
# Ollama-compatible chat via DMR
# Usage: dmr-ollama.sh [model] "your prompt"
# Default model: ai/smollm2
set -euo pipefail

MODEL="${1:-ai/smollm2}"
PROMPT="${2:-Hello!}"

echo "Model: $MODEL (Ollama API)"
echo "Prompt: $PROMPT"
echo "Response:"

curl -s http://localhost:12434/api/chat \
  -H "Content-Type: application/json" \
  -d "$(python3 -c "
import json
print(json.dumps({
    'model': '$MODEL',
    'messages': [{'role': 'user', 'content': '$PROMPT'}],
    'stream': False,
}))
")" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
    content = d.get("message", {}).get("content", "") or d.get("response", "")
    print(content)
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
'
