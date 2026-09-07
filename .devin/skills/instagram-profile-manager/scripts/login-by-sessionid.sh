#!/usr/bin/env bash
# Login to Instagram sidecar using a sessionid
# Usage: login-by-sessionid.sh <sessionid>
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

SID="${1:?Usage: login-by-sessionid.sh <sessionid>}"
API="${INSTAGRAM_SIDECAR_URL:-http://localhost:8011}"

echo "Logging in to Instagram via sessionid..."

RESP=$(curl -sf -X POST "$API/auth/login/by/sessionid" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "sessionid=$SID" 2>&1) || {
  echo "Login failed. Trying JSON body..."
  RESP=$(curl -sf -X POST "$API/auth/login/by/sessionid" \
    -H "Content-Type: application/json" \
    -d "{\"sessionid\": \"$SID\"}" 2>&1)
}

echo "$RESP" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    sid = d.get('session_id') or d.get('sessionid') or 'N/A'
    print(f'Session ID: {sid}')
    print(f'Status: {d.get(\"status\", \"ok\")}')
    user = d.get('user', {})
    if user:
        print(f'Username: {user.get(\"username\", \"N/A\")}')
        print(f'PK: {user.get(\"pk\", \"N/A\")}')
        print(f'Full name: {user.get(\"full_name\", \"N/A\")}')
        print(f'Biography: {user.get(\"biography\", \"N/A\")[:80]}')
except:
    print(sys.stdin.read())
"
