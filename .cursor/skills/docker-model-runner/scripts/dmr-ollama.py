#!/usr/bin/env python3
"""Ollama-compatible chat via DMR.
Usage: dmr-ollama.py [model] "your prompt"
Default model: ai/smollm2"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import dmr_post  # noqa: E402

model = sys.argv[1] if len(sys.argv) > 1 else "ai/smollm2"
prompt = sys.argv[2] if len(sys.argv) > 2 else "Hello!"

print(f"Model: {model} (Ollama API)")
print(f"Prompt: {prompt}")
print("Response:")
d = dmr_post("/api/chat", {
    "model": model,
    "messages": [{"role": "user", "content": prompt}],
    "stream": False,
})
if "error" in d and len(d) == 1:
    print(f'Error: {d["error"]}', file=sys.stderr)
    sys.exit(1)
print(d.get("message", {}).get("content") or d.get("response", d))
