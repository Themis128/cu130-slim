#!/usr/bin/env bash
# Quick chat with a DMR model
# Usage: dmr-chat.sh [model] "your prompt"
# Default model: ai/smollm2 (fast, small)
set -euo pipefail

MODEL="${1:-ai/smollm2}"
PROMPT="${2:-Hello!}"

echo "Model: $MODEL"
echo "Prompt: $PROMPT"
echo "Response:"

curl -s http://localhost:12434/engines/llama.cpp/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d "$(python3 -c "
import json, sys
print(json.dumps({
    'model': '$MODEL',
    'messages': [{'role': 'user', 'content': '$PROMPT'}],
    'max_tokens': 512,
    'temperature': 0.7,
}))
")" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
    print(d["choices"][0]["message"]["content"])
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
'
