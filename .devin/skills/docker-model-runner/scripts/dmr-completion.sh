#!/usr/bin/env bash
# Text completion (not chat) via DMR OpenAI-compatible API
# Usage: dmr-completion.sh [model] "your prompt"
# Default model: ai/smollm2
set -euo pipefail

MODEL="${1:-ai/smollm2}"
PROMPT="${2:?Usage: dmr-completion.sh [model] \"prompt\"}"

echo "Model: $MODEL"
echo "Prompt: $PROMPT"
echo "Response:"

curl -s http://localhost:12434/engines/llama.cpp/v1/completions \
  -H "Content-Type: application/json" \
  -d "$(python3 -c "
import json
print(json.dumps({
    'model': '$MODEL',
    'prompt': '$PROMPT',
    'max_tokens': 256,
    'temperature': 0.7,
}))
")" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
    print(d["choices"][0]["text"])
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
'
