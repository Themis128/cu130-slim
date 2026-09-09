#!/usr/bin/env bash
# Start the SocialAuto OAuth flow for Threads and print the VNC/authorize URL.
set -euo pipefail

cd /home/tbaltzakis/cu130-slim

TEAM_ID="88e2bab4-3581-4c04-b0ac-87aa27840025"
ADMIN_PASSWORD=$(grep "^SOCIAL_ADMIN_PASSWORD=" .env | cut -d= -f2)
API="http://localhost:8083"

echo "=== Logging in to SocialAuto ==="
TOKEN=$(curl -s -X POST "$API/api/v1/auth/login" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d "username=tbaltzakis@cloudless.gr&password=${ADMIN_PASSWORD}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))")

if [ -z "$TOKEN" ]; then
  echo "Could not log in to SocialAuto"
  exit 1
fi

echo "=== Generating Threads OAuth URL ==="
AUTH_URL=$(curl -s -H "Authorization: Bearer $TOKEN" \
  "$API/api/v1/auth/oauth/threads/authorize?team_id=$TEAM_ID" | python3 -c "import sys,json; print(json.load(sys.stdin).get('authorization_url',''))")

if [ -z "$AUTH_URL" ]; then
  echo "Could not generate OAuth URL"
  exit 1
fi

echo ""
echo "Authorize URL: $AUTH_URL"
echo ""

# Ask whether to auto-open in browser bridge
AUTO_OPEN=${1:-""}
if [ "$AUTO_OPEN" = "--open" ]; then
  echo "=== Opening in browser-novnc ==="
  curl -s -X POST http://localhost:9223/session/navigate \
    -H 'Content-Type: application/json' \
    -d "{\"url\":\"$AUTH_URL\"}" | python3 -m json.tool
  echo ""
  echo "Open noVNC at http://localhost:6080/vnc.html to see the consent dialog."
else
  echo "To auto-open in the VNC browser, run:"
  echo "  $0 --open"
  echo ""
  echo "Open noVNC at http://localhost:6080/vnc.html and navigate to the Authorize URL above."
fi
