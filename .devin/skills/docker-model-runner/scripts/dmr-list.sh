#!/usr/bin/env bash
# List all local DMR models with details
set -euo pipefail

echo "=== Docker Model Runner — Local Models ==="
echo ""

# Get model list
docker model list 2>/dev/null

echo ""
echo "=== Loaded in Memory ==="
curl -sf http://localhost:12434/engines/v1/models 2>/dev/null | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
    models = d.get("data", [])
    if not models:
        print("  (no models currently loaded in memory)")
    for m in models:
        mid = m.get("id", "?")
        print("  ✓ %s" % mid)
except:
    print("  (DMR API not reachable)")
'

echo ""
echo "=== Available on Docker Hub (ai namespace) ==="
echo "  Browse: https://hub.docker.com/u/ai"
echo "  Pull:   docker model pull ai/<model-name>"
echo ""
echo "  Popular models:"
echo "    ai/qwen3:8b-q4_K_M       — General text (8B, quantized)"
echo "    ai/qwen3-vl              — Vision/multimodal (8B)"
echo "    ai/qwen3-embedding       — Embeddings"
echo "    ai/smollm2               — Tiny/fast (360M)"
echo "    ai/llama3.2              — Meta Llama 3.2"
echo "    ai/qwen2.5-coder         — Code generation"
echo "    ai/gemma3                — Google Gemma 3"
echo ""
echo "  From Hugging Face:"
echo "    docker model pull hf.co/<org>/<model>"
