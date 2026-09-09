#!/usr/bin/env bash
# Vision (multimodal) request via DMR — send an image + text prompt
# Usage: dmr-vision.sh <image_path> "question about the image" [model]
# Default model: ai/qwen3-vl
# Example: dmr-vision.sh /tmp/photo.jpg "What objects are in this image?"
set -euo pipefail

IMAGE="${1:?Usage: dmr-vision.sh <image_path> \"prompt\" [model]}"
PROMPT="${2:?Usage: dmr-vision.sh <image_path> \"prompt\" [model]}"
MODEL="${3:-ai/qwen3-vl}"

if [[ ! -f "$IMAGE" ]]; then
    echo "Error: Image not found: $IMAGE" >&2
    exit 1
fi

echo "Model: $MODEL"
echo "Image: $IMAGE"
echo "Prompt: $PROMPT"
echo "Response:"

# Base64-encode the image and send as data URI
python3 -c "
import base64, json, os, sys, urllib.request

image_path = sys.argv[1]
prompt = sys.argv[2]
model = sys.argv[3]

with open(image_path, 'rb') as f:
    img_b64 = base64.b64encode(f.read()).decode('utf-8')

ext = os.path.splitext(image_path)[1].lower()
mime_map = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.webp': 'image/webp'}
mime = mime_map.get(ext, 'image/png')

body = json.dumps({
    'model': model,
    'messages': [{
        'role': 'user',
        'content': [
            {'type': 'text', 'text': prompt},
            {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{img_b64}'}},
        ],
    }],
    'max_tokens': 512,
}).encode('utf-8')

req = urllib.request.Request(
    'http://localhost:12434/engines/llama.cpp/v1/chat/completions',
    data=body,
    method='POST',
    headers={'Content-Type': 'application/json'},
)
try:
    with urllib.request.urlopen(req, timeout=180) as resp:
        d = json.loads(resp.read().decode('utf-8'))
        print(d['choices'][0]['message']['content'])
except Exception as e:
    print(f'Error: {e}', file=sys.stderr)
    sys.exit(1)
" "$IMAGE" "$PROMPT" "$MODEL"
