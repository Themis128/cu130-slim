#!/usr/bin/env bash
# Update Instagram biography via aiograpi-rest.
# Usage: update-bio.sh <session_id> "new bio text"
set -euo pipefail

SIDECAR_URL="${INSTAGRAM_PRIVATE_API_URL:-http://localhost:8011}"

SESSION_ID="${1:?Usage: update-bio.sh <session_id> \"new bio\"}"
BIO="${2:?Usage: update-bio.sh <session_id> \"new bio\"}"

echo "Updating Instagram biography..."

RESP=$(curl -sf -X PATCH "${SIDECAR_URL}/account/biography" \
  -H "X-Session-ID: ${SESSION_ID}" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "biography=${BIO}" 2>&1 || true)

echo "Response: $RESP"
echo "✓ Biography updated (if no error above)"
