#!/usr/bin/env bash
# Anthropic-compatible API via DMR
# Usage: dmr-anthropic.sh [model] "your prompt" [system_prompt]
# Default model: ai/smollm2
set -euo pipefail

MODEL="${1:-ai/smollm2}"
PROMPT="${2:-Hello!}"
SYSTEM="${3:-}"

echo "Model: $MODEL (Anthropic API)"
echo "Prompt: $PROMPT"
${SYSTEM:+echo "System: $SYSTEM"}
echo "Response:"

python3 -c "
import json, sys, urllib.request

model = sys.argv[1]
prompt = sys.argv[2]
system = sys.argv[3]

body = {
    'model': model,
    'max_tokens': 1024,
    'messages': [{'role': 'user', 'content': prompt}],
}
if system:
    body['system'] = system

data = json.dumps(body).encode('utf-8')
req = urllib.request.Request(
    'http://localhost:12434/anthropic/v1/messages',
    data=data,
    method='POST',
    headers={'Content-Type': 'application/json'},
)
try:
    with urllib.request.urlopen(req, timeout=120) as resp:
        d = json.loads(resp.read().decode('utf-8'))
        content = d.get('content', [{}])[0].get('text', '')
        print(content)
except Exception as e:
    print(f'Error: {e}', file=sys.stderr)
    sys.exit(1)
" "$MODEL" "$PROMPT" "$SYSTEM"
