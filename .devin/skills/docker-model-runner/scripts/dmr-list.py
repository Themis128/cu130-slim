#!/usr/bin/env python3
"""List all local DMR models with details."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import dmr_get, docker_model  # noqa: E402

print("=== Docker Model Runner — Local Models ===\n")
docker_model("list")

print("\n=== Loaded in Memory ===")
raw = dmr_get("/engines/v1/models")
if raw is None:
    print("  (DMR API not reachable)")
else:
    models = json.loads(raw).get("data", [])
    if not models:
        print("  (no models currently loaded in memory)")
    for m in models:
        print(f'  ✓ {m.get("id", "?")}')

print("""
=== Available on Docker Hub (ai namespace) ===
  Browse: https://hub.docker.com/u/ai
  Pull:   docker model pull ai/<model-name>

  Popular models:
    ai/qwen3:8b-q4_K_M       — General text (8B, quantized)
    ai/qwen3-vl              — Vision/multimodal (8B)
    hf.co/Qwen/Qwen3-Embedding-0.6B-GGUF — Embeddings
    ai/smollm2               — Tiny/fast (360M)
    ai/llama3.2              — Meta Llama 3.2
    ai/qwen2.5-coder         — Code generation
    ai/gemma3                — Google Gemma 3

  From Hugging Face:
    docker model pull hf.co/<org>/<model>""")
