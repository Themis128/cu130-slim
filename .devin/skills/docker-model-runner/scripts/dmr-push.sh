#!/usr/bin/env bash
# Push a local DMR model to Docker Hub or HuggingFace
# Usage: dmr-push.sh <model>
# Example: dmr-push.sh myorg/mymodel:latest
set -euo pipefail

MODEL="${1:?Usage: dmr-push.sh <model>}"

echo "Pushing model: $MODEL"
echo "This may take a while for large models..."
echo ""

docker model push "$MODEL"
echo ""
echo "=== Push complete ==="
