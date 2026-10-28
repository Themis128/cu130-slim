#!/usr/bin/env python3
"""Anthropic-compatible API via DMR.
Usage: dmr-anthropic.py [model] "your prompt" [system_prompt]
Default model: ai/smollm2"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import dmr_post  # noqa: E402

model = sys.argv[1] if len(sys.argv) > 1 else "ai/smollm2"
prompt = sys.argv[2] if len(sys.argv) > 2 else "Hello!"
system = sys.argv[3] if len(sys.argv) > 3 else ""

print(f"Model: {model} (Anthropic API)")
print(f"Prompt: {prompt}")
if system:
    print(f"System: {system}")
print("Response:")

body = {
    "model": model,
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": prompt}],
}
if system:
    body["system"] = system

d = dmr_post("/anthropic/v1/messages", body)
if "error" in d and len(d) == 1:
    print(f'Error: {d["error"]}', file=sys.stderr)
    sys.exit(1)
print(d.get("content", [{}])[0].get("text", d))
