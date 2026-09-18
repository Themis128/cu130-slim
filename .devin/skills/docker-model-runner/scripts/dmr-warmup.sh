#!/usr/bin/env bash
# Warm the two hot-path models so first user request is fast.
# 4B instruct (chatbots) first, then 8B (long-form). Both fit together
# (~7.8GB) on the RTX 3070.
set -euo pipefail
BASE="${DMR_BASE:-http://localhost:12435}/engines/llama.cpp/v1/chat/completions"
warm() {
    local model="$1"
    echo "Warming $model ..."
    curl -sf --max-time 90 "$BASE" -H 'Content-Type: application/json' \
      -d "{\"model\":\"$model\",\"messages\":[{\"role\":\"user\",\"content\":\"Hi\"}],\"max_tokens\":3}" \
      >/dev/null && echo "  loaded" || echo "  FAILED"
}
warm "hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M"
warm "ai/qwen3:8b-q4_K_M"
echo ""
command -v nvidia-smi >/dev/null && nvidia-smi --query-gpu=memory.used --format=csv,noheader | xargs -I{} echo "VRAM used: {}"
