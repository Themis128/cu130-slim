#!/usr/bin/env bash
# Login to Instagram via aiograpi-rest sidecar using credentials from the secret store.
# Usage: login.sh [proxy_url]
set -euo pipefail

SIDECAR_URL="${INSTAGRAM_PRIVATE_API_URL:-http://localhost:8011}"
API_URL="${SOCIAL_API_URL:-http://localhost:8083}"

# Get admin token
ADMIN_EMAIL=$(grep -E '^SOCIAL_ADMIN_EMAIL=' .env | cut -d= -f2-)
ADMIN_PASS=$(grep -E '^SOCIAL_ADMIN_PASSWORD=' .env | cut -d= -f2-)
TOKEN=$(curl -sf -X POST "${API_URL}/api/v1/auth/login" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=${ADMIN_EMAIL}" \
  --data-urlencode "password=${ADMIN_PASS}" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

# Get credentials from secret store (masked API returns raw value for GET)
INSTA_USER=$(curl -sf "${API_URL}/api/v1/secrets/INSTAGRAM_USERNAME" \
  -H "Authorization: Bearer ${TOKEN}" | python3 -c 'import sys,json; print(json.load(sys.stdin)["value"])')
INSTA_PASS=$(curl -sf "${API_URL}/api/v1/secrets/INSTAGRAM_PASSWORD" \
  -H "Authorization: Bearer ${TOKEN}" | python3 -c 'import sys,json; print(json.load(sys.stdin)["value"])')

if [ -z "$INSTA_USER" ] || [ -z "$INSTA_PASS" ]; then
  echo "✗ Instagram credentials not found in secret store"
  echo "  Save them with: .devin/skills/social-profile-secrets/scripts/set-instagram.sh <user> <pass>"
  exit 1
fi

PROXY="${1:-}"
LOCALE="el_GR"
TIMEZONE="10800"

echo "Logging in to Instagram as ${INSTA_USER} (locale=${LOCALE}, tz=${TIMEZONE})..."

DATA="username=${INSTA_USER}&password=${INSTA_PASS}&locale=${LOCALE}&timezone=${TIMEZONE}"
if [ -n "$PROXY" ]; then
  DATA="${DATA}&proxy=${PROXY}"
  echo "  Using proxy: ${PROXY}"
fi

RESP=$(curl -sf -X POST "${SIDECAR_URL}/auth/login" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data "${DATA}" 2>&1 || true)

# Check if it's a plain string (session_id) or JSON error
if echo "$RESP" | python3 -c 'import sys; s=sys.stdin.read().strip(); print("STRING" if s.startswith("\"") and s.endswith("\"") else "JSON" if s.startswith("{") else "RAW")' 2>/dev/null | grep -q "STRING"; then
  SESSION_ID=$(echo "$RESP" | python3 -c 'import sys,json; print(json.load(sys.stdin))')
  echo "✓ Login successful!"
  echo "  Session ID: ${SESSION_ID:0:20}..."
  echo "$SESSION_ID" > /tmp/instagram_session_id
  echo "  Saved to /tmp/instagram_session_id"
else
  echo "Response: $RESP"
  echo "$RESP" | python3 -c '
import sys, json
try:
  d = json.load(sys.stdin)
  exc = d.get("exc_type", "")
  if exc == "ChallengeRequired":
    print("✗ Challenge required — Instagram sent a security code via SMS/email.")
    print("  Use: login-2fa.sh <code>  or  challenge-resolve.sh <session_id> <last_json> <code>")
  elif exc == "TwoFactorRequired":
    print("✗ 2FA required — provide TOTP/SMS code.")
    print("  Use: login-2fa.sh <code>")
  else:
    print("✗ Login failed:", d.get("detail", "unknown"))
except: pass
' 2>/dev/null || true
fi
