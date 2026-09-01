#!/usr/bin/env bash
# Check aiograpi-rest sidecar health
set -euo pipefail

SIDECAR_URL="${INSTAGRAM_PRIVATE_API_URL:-http://localhost:8011}"

echo "Checking sidecar at $SIDECAR_URL..."
RESP=$(curl -sf "${SIDECAR_URL}/health" 2>/dev/null || echo "FAILED")
echo "$RESP"

if echo "$RESP" | grep -q '"ok"'; then
  echo "✓ Sidecar is healthy"
  # Also show build info
  BUILD=$(curl -sf "${SIDECAR_URL}/build" 2>/dev/null || echo "{}")
  echo "Build: $BUILD"
  exit 0
else
  echo "✗ Sidecar is not responding"
  exit 1
fi
