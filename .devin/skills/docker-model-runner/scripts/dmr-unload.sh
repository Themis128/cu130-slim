#!/usr/bin/env bash
# Unload running models from memory to free VRAM
# Usage: dmr-unload.sh [--all | model1 model2 ... | --backend llama.cpp]
set -euo pipefail

if [[ "${1:-}" == "--all" ]]; then
    echo "Unloading ALL running models..."
    # Try API first
    if curl -sf http://localhost:12434/inference/unload >/dev/null 2>&1; then
        curl -sf -X POST http://localhost:12434/inference/unload \
          -H "Content-Type: application/json" \
          -d '{"all": true}' | python3 -m json.tool 2>/dev/null || true
    else
        docker model unload --all
    fi
elif [[ "${1:-}" == "--backend" ]]; then
    BACKEND="${2:?Usage: dmr-unload.sh --backend <backend>}"
    echo "Unloading all models for backend: $BACKEND..."
    if curl -sf http://localhost:12434/inference/unload >/dev/null 2>&1; then
        curl -sf -X POST http://localhost:12434/inference/unload \
          -H "Content-Type: application/json" \
          -d "{\"backend\": \"$BACKEND\"}" | python3 -m json.tool 2>/dev/null || true
    else
        docker model unload --backend "$BACKEND"
    fi
elif [[ $# -gt 0 ]]; then
    echo "Unloading models: $*"
    if curl -sf http://localhost:12434/inference/unload >/dev/null 2>&1; then
        MODELS=$(python3 -c "import json,sys; print(json.dumps(sys.argv[1:]))" "$@")
        curl -sf -X POST http://localhost:12434/inference/unload \
          -H "Content-Type: application/json" \
          -d "{\"models\": $MODELS}" | python3 -m json.tool 2>/dev/null || true
    else
        docker model unload "$@"
    fi
else
    echo "Usage: dmr-unload.sh [--all | model1 model2 ... | --backend <backend>]"
    echo "  --all              Unload all running models"
    echo "  --backend <name>   Unload all models for a backend"
    echo "  model1 model2 ...  Unload specific models"
    exit 1
fi

echo "Done."
