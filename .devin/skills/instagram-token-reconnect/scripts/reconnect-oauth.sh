#!/usr/bin/env bash
# Generate the Instagram OAuth reconnection URL and open it in the browser bridge.
# Usage: reconnect-oauth.sh <account_id>
set -euo pipefail

ACCOUNT_ID="${1:-}"
if [[ -z "$ACCOUNT_ID" ]]; then
  echo "Usage: $0 <account_id>" >&2
  exit 1
fi

cd /home/tbaltzakis/cu130-slim
source .env 2>/dev/null || true

TOKEN=$(curl -s -X POST http://localhost:8083/api/v1/auth/login \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d "username=${SOCIAL_ADMIN_EMAIL}&password=${SOCIAL_ADMIN_PASSWORD}" \
  2>/dev/null | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('access_token',''))" 2>/dev/null)

if [[ -z "$TOKEN" ]]; then
  echo "Error: Could not authenticate with SocialAuto" >&2
  exit 1
fi

echo "=== Instagram OAuth Reconnection for account $ACCOUNT_ID ===" >&2

# Get the OAuth URL from SocialAuto
OAUTH_URL=$(curl -s "http://localhost:8083/api/v1/auth/oauth/instagram/authorize" \
  -H "Authorization: Bearer $TOKEN" 2>/dev/null | python3 -c "
import sys, json
d = json.loads(sys.stdin.read())
print(d.get('authorization_url', d.get('url', d.get('auth_url', ''))))
" 2>/dev/null)

if [[ -z "$OAUTH_URL" ]]; then
  echo "Error: Could not get OAuth URL from SocialAuto" >&2
  echo "Check that the Instagram OAuth flow is configured." >&2
  exit 1
fi

echo "OAuth URL: $OAUTH_URL" >&2
echo "" >&2
echo "Opening in browser-novnc..." >&2

# Navigate the browser bridge to the OAuth URL
curl -s -X POST "http://localhost:9223/session/navigate" \
  -H "Content-Type: application/json" \
  -d "{\"url\": \"$OAUTH_URL\"}" >/dev/null 2>&1

echo "Browser navigated to OAuth URL." >&2
echo "Complete the login via noVNC at http://localhost:6080/vnc.html" >&2
echo "After granting permissions, the callback will store the new token." >&2
