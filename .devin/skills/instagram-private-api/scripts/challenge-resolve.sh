#!/usr/bin/env bash
# Resolve an Instagram challenge with a security code.
# Usage: challenge-resolve.sh <session_id> <last_json> <security_code>
set -euo pipefail

SIDECAR_URL="${INSTAGRAM_PRIVATE_API_URL:-http://localhost:8011}"

SESSION_ID="${1:?Usage: challenge-resolve.sh <session_id> <last_json> <security_code>}"
LAST_JSON="${2:?Usage: challenge-resolve.sh <session_id> <last_json> <security_code>}"
SECURITY_CODE="${3:?Usage: challenge-resolve.sh <session_id> <last_json> <security_code>}"

echo "Resolving Instagram challenge..."

RESP=$(curl -sf -X POST "${SIDECAR_URL}/auth/challenge/resolve" \
  -H "X-Session-ID: ${SESSION_ID}" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "last_json=${LAST_JSON}" \
  --data-urlencode "security_code=${SECURITY_CODE}" 2>&1 || true)

echo "Response: $RESP"

if echo "$RESP" | grep -q "true"; then
  echo "✓ Challenge resolved successfully!"
  echo "  Retry login now: .devin/skills/instagram-private-api/scripts/login.sh"
else
  echo "✗ Challenge resolution failed"
fi
