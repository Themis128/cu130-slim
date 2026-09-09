#!/usr/bin/env bash
# Tag a local DMR model with a new name
# Usage: dmr-tag.sh <source> <target>
# Example: dmr-tag.sh ai/smollm2 myorg/smollm2:latest
set -euo pipefail

SOURCE="${1:?Usage: dmr-tag.sh <source> <target>}"
TARGET="${2:?Usage: dmr-tag.sh <source> <target>}"

echo "Tagging: $SOURCE → $TARGET"
docker model tag "$SOURCE" "$TARGET"
echo "Done."
