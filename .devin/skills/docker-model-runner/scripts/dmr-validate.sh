#!/usr/bin/env bash
# Check all SocialAuto-expected DMR models are pulled.
set -euo pipefail
EXPECTED=(
    "ai/qwen3:8b-q4_K_M"
    "hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M"
    "ai/smollm3"
    "ai/qwen3-vl"
    "ai/qwen3-embedding"
)
MODELS=$(curl -sf http://localhost:12435/engines/v1/models 2>/dev/null) || {
    echo "DMR API offline"; exit 1; }
MISSING=0
for e in "${EXPECTED[@]}"; do
    n=$(echo "$e" | tr 'A-Z' 'a-z' | sed 's|hf\.co/|huggingface.co/|')
    if echo "$MODELS" | tr 'A-Z' 'a-z' | grep -q "${n##*/}\|$n"; then
        echo "  OK       $e"
    else
        echo "  MISSING  $e"; MISSING=$((MISSING+1))
    fi
done
echo ""
[[ $MISSING -eq 0 ]] && echo "All expected models present" || { echo "$MISSING missing"; exit 1; }
