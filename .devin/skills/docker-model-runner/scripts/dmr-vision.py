#!/usr/bin/env python3
"""Vision (multimodal) request via DMR — send an image + text prompt.
Usage: dmr-vision.py <image_path> "question about the image" [model]
Default model: ai/qwen3-vl"""

import base64
import mimetypes
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import dmr_post  # noqa: E402
from skill_http import usage  # noqa: E402

if len(sys.argv) < 3:
    usage('dmr-vision.py <image_path> "prompt" [model]')
image_path, prompt = sys.argv[1], sys.argv[2]
model = sys.argv[3] if len(sys.argv) > 3 else "ai/qwen3-vl"

img = Path(image_path)
if not img.is_file():
    print(f"Error: Image not found: {image_path}", file=sys.stderr)
    sys.exit(1)

print(f"Model: {model}")
print(f"Image: {image_path}")
print(f"Prompt: {prompt}")
print("Response:")

mime = mimetypes.guess_type(str(img))[0] or "image/png"
b64 = base64.b64encode(img.read_bytes()).decode()
d = dmr_post("/engines/llama.cpp/v1/chat/completions", {
    "model": model,
    "messages": [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ],
    }],
    "max_tokens": 512,
}, timeout=180)
if "error" in d and len(d) == 1:
    print(f'Error: {d["error"]}', file=sys.stderr)
    sys.exit(1)
print(d.get("choices", [{}])[0].get("message", {}).get("content", d))
