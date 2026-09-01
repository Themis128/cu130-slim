#!/usr/bin/env bash
# Update Instagram profile picture via aiograpi-rest.
# Usage: update-picture.sh <session_id> <image_file>
set -euo pipefail

SIDECAR_URL="${INSTAGRAM_PRIVATE_API_URL:-http://localhost:8011}"

SESSION_ID="${1:?Usage: update-picture.sh <session_id> <image_file>}"
IMAGE_FILE="${2:?Usage: update-picture.sh <session_id> <image_file>}"

if [ ! -f "$IMAGE_FILE" ]; then
  echo "✗ Image file not found: $IMAGE_FILE"
  exit 1
fi

echo "Uploading profile picture..."

RESP=$(curl -sf -X PATCH "${SIDECAR_URL}/account/picture" \
  -H "X-Session-ID: ${SESSION_ID}" \
  -F "picture=@${IMAGE_FILE};type=image/jpeg" 2>&1 || true)

echo "Response: $RESP"
echo "✓ Profile picture updated (if no error above)"
