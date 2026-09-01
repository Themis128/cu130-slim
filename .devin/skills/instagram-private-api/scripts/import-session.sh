#!/usr/bin/env bash
# Import an existing Instagram sessionid cookie to bypass login/challenge.
# Usage: import-session.sh <sessionid>
set -euo pipefail

SIDECAR_URL="${INSTAGRAM_PRIVATE_API_URL:-http://localhost:8011}"

SESSION_ID="${1:?Usage: import-session.sh <sessionid>}"
LOCALE="el_GR"
TIMEZONE="10800"

echo "Importing Instagram session..."

RESP=$(curl -sf -X POST "${SIDECAR_URL}/auth/login/by/sessionid" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data "sessionid=${SESSION_ID}&locale=${LOCALE}&timezone=${TIMEZONE}" 2>&1 || true)

if echo "$RESP" | python3 -c 'import sys; s=sys.stdin.read().strip(); exit(0 if s.startswith("\"") else 1)' 2>/dev/null; then
  NEW_SESSION_ID=$(echo "$RESP" | python3 -c 'import sys,json; print(json.load(sys.stdin))')
  echo "✓ Session imported successfully!"
  echo "  New Session ID: ${NEW_SESSION_ID:0:20}..."
  echo "$NEW_SESSION_ID" > /tmp/instagram_session_id
else
  echo "Response: $RESP"
  echo "✗ Session import failed — the sessionid may be expired"
fi
