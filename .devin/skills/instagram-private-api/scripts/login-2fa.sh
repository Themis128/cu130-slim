#!/usr/bin/env bash
# Login to Instagram with 2FA verification code.
# Usage: login-2fa.sh <verification_code> [proxy_url]
set -euo pipefail

SIDECAR_URL="${INSTAGRAM_PRIVATE_API_URL:-http://localhost:8011}"
API_URL="${SOCIAL_API_URL:-http://localhost:8083}"

CODE="${1:?Usage: login-2fa.sh <verification_code> [proxy_url]}"
PROXY="${2:-}"

# Get admin token
ADMIN_EMAIL=$(grep -E '^SOCIAL_ADMIN_EMAIL=' .env | cut -d= -f2-)
ADMIN_PASS=$(grep -E '^SOCIAL_ADMIN_PASSWORD=' .env | cut -d= -f2-)
TOKEN=$(curl -sf -X POST "${API_URL}/api/v1/auth/login" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=${ADMIN_EMAIL}" \
  --data-urlencode "password=${ADMIN_PASS}" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

INSTA_USER=$(curl -sf "${API_URL}/api/v1/secrets/INSTAGRAM_USERNAME" \
  -H "Authorization: Bearer ${TOKEN}" | python3 -c 'import sys,json; print(json.load(sys.stdin)["value"])')
INSTA_PASS=$(curl -sf "${API_URL}/api/v1/secrets/INSTAGRAM_PASSWORD" \
  -H "Authorization: Bearer ${TOKEN}" | python3 -c 'import sys,json; print(json.load(sys.stdin)["value"])')

LOCALE="el_GR"
TIMEZONE="10800"

echo "Logging in to Instagram with 2FA code..."

DATA="username=${INSTA_USER}&password=${INSTA_PASS}&verification_code=${CODE}&locale=${LOCALE}&timezone=${TIMEZONE}"
if [ -n "$PROXY" ]; then
  DATA="${DATA}&proxy=${PROXY}"
fi

RESP=$(curl -sf -X POST "${SIDECAR_URL}/auth/login" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data "${DATA}" 2>&1 || true)

if echo "$RESP" | python3 -c 'import sys; s=sys.stdin.read().strip(); exit(0 if s.startswith("\"") else 1)' 2>/dev/null; then
  SESSION_ID=$(echo "$RESP" | python3 -c 'import sys,json; print(json.load(sys.stdin))')
  echo "✓ Login successful with 2FA!"
  echo "  Session ID: ${SESSION_ID:0:20}..."
  echo "$SESSION_ID" > /tmp/instagram_session_id
else
  echo "Response: $RESP"
  echo "✗ Login with 2FA failed"
fi
