#!/usr/bin/env python3
"""Text completion (not chat) via DMR OpenAI-compatible API.
Usage: dmr-completion.py [model] "your prompt"
Default model: ai/smollm2"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import dmr_post  # noqa: E402
from skill_http import usage  # noqa: E402

model = sys.argv[1] if len(sys.argv) > 1 else "ai/smollm2"
prompt = sys.argv[2] if len(sys.argv) > 2 else usage('dmr-completion.py [model] "prompt"')

print(f"Model: {model}")
print(f"Prompt: {prompt}")
print("Response:")
d = dmr_post("/engines/llama.cpp/v1/completions", {
    "model": model, "prompt": prompt, "max_tokens": 256, "temperature": 0.7})
if "error" in d and len(d) == 1:
    print(f'Error: {d["error"]}', file=sys.stderr)
    sys.exit(1)
print(d.get("choices", [{}])[0].get("text", d))
