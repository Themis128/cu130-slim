#!/usr/bin/env bash
# Save aiograpi client settings for session restore.
# Usage: save-settings.sh <session_id>
set -euo pipefail

SIDECAR_URL="${INSTAGRAM_PRIVATE_API_URL:-http://localhost:8011}"

SESSION_ID="${1:?Usage: save-settings.sh <session_id>}"
OUTPUT="${2:-/tmp/instagram_settings.json}"

echo "Saving settings for session ${SESSION_ID:0:20}..."

curl -sf "${SIDECAR_URL}/auth/settings" \
  -H "X-Session-ID: ${SESSION_ID}" \
  -o "$OUTPUT" 2>&1 || true

if [ -s "$OUTPUT" ]; then
  echo "✓ Settings saved to $OUTPUT"
  echo "  Size: $(wc -c < "$OUTPUT") bytes"
else
  echo "✗ Failed to save settings"
fi
