#!/usr/bin/env python3
"""Generate embeddings via DMR.
Usage: dmr-embed.py "text to embed" [model]
Default model: hf.co/Qwen/Qwen3-Embedding-0.6B-GGUF"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import dmr_post  # noqa: E402
from skill_http import usage  # noqa: E402

text = sys.argv[1] if len(sys.argv) > 1 else usage('dmr-embed.py "text to embed" [model]')
model = sys.argv[2] if len(sys.argv) > 2 else "hf.co/Qwen/Qwen3-Embedding-0.6B-GGUF"

print(f"Model: {model}")
print(f"Input: {text}\n")
d = dmr_post("/engines/llama.cpp/v1/embeddings", {"model": model, "input": text})
if "error" in d and len(d) == 1:
    print(f'Error: {d["error"]}', file=sys.stderr)
    sys.exit(1)
try:
    emb = d["data"][0]["embedding"]
    print(f"Dimensions: {len(emb)}")
    print(f"First 5: {emb[:5]}")
    print(f'Model: {d.get("model", "?")}')
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
