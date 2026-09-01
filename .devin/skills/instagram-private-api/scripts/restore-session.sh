#!/usr/bin/env bash
# Restore an aiograpi session from saved settings JSON (no password needed).
# Usage: restore-session.sh <settings_json_file>
set -euo pipefail

SIDECAR_URL="${INSTAGRAM_PRIVATE_API_URL:-http://localhost:8011}"

SETTINGS_FILE="${1:?Usage: restore-session.sh <settings_json_file>}"

if [ ! -f "$SETTINGS_FILE" ]; then
  echo "✗ Settings file not found: $SETTINGS_FILE"
  exit 1
fi

SETTINGS_JSON=$(cat "$SETTINGS_FILE")

echo "Restoring session from settings..."

RESP=$(curl -sf -X PATCH "${SIDECAR_URL}/auth/settings" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "settings=${SETTINGS_JSON}" 2>&1 || true)

if echo "$RESP" | python3 -c 'import sys; s=sys.stdin.read().strip(); exit(0 if s.startswith("\"") else 1)' 2>/dev/null; then
  SESSION_ID=$(echo "$RESP" | python3 -c 'import sys,json; print(json.load(sys.stdin))')
  echo "✓ Session restored successfully!"
  echo "  Session ID: ${SESSION_ID:0:20}..."
  echo "$SESSION_ID" > /tmp/instagram_session_id
else
  echo "Response: $RESP"
  echo "✗ Session restore failed — settings may be expired"
fi
