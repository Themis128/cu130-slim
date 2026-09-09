#!/usr/bin/env bash
# Remove a local DMR model
# Usage: dmr-rm.sh <model> [--force]
# Example: dmr-rm.sh ai/old-model --force
set -euo pipefail

MODEL="${1:?Usage: dmr-rm.sh <model> [--force]}"
shift

FORCE=""
if [[ "${1:-}" == "--force" || "${1:-}" == "-f" ]]; then
    FORCE="-f"
fi

echo "Removing model: $MODEL ${FORCE:+(force)}"
docker model rm $FORCE "$MODEL"
echo "Done."
