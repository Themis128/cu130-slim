#!/usr/bin/env bash
# Update Instagram external URL via instagrapi sidecar
# Usage: ig-update-url.sh "https://cloudless.gr"
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

URL="${1:?Usage: ig-update-url.sh <url>}"
API="http://localhost:8011"

# Get session ID
SID="${IG_SESSION_ID:-}"
if [ -z "$SID" ] && [ -f /tmp/ig-sidecar-session.txt ]; then
  SID=$(cat /tmp/ig-sidecar-session.txt)
fi
if [ -z "$SID" ]; then
  echo "→ No session found, logging in first..."
  bash "$ROOT/.devin/skills/social-accounts-manager/scripts/ig-login.sh"
  SID=$(cat /tmp/ig-sidecar-session.txt 2>/dev/null || echo "")
  if [ -z "$SID" ]; then
    echo "  ERROR: Could not get Instagram session."
    exit 1
  fi
fi

echo "→ Updating Instagram external URL to $URL..."

curl -sf -X PATCH "$API/account/external-url" \
  -H "X-Session-ID: $SID" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "external_url=$URL" 2>&1 | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(f'  Result: {d.get(\"status\", \"ok\")}')
    print(f'  URL: {d.get(\"external_url\", \"N/A\")}')
except:
    print(sys.stdin.read()[:200])
"
