#!/usr/bin/env bash
# Update Instagram bio (150 char limit, emojis count as 2) via instagrapi sidecar
# Usage: ig-update-bio.sh "Bio text"
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

BIO="${1:?Usage: ig-update-bio.sh <bio>}"
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

echo "→ Updating Instagram bio (${#BIO} chars)..."

curl -sf -X PATCH "$API/account/biography" \
  -H "X-Session-ID: $SID" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "biography=$BIO" 2>&1 | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(f'  Result: {d.get(\"status\", \"ok\")}')
    print(f'  Bio: {d.get(\"biography\", d.get(\"updated\", \"N/A\"))[:80]}')
except:
    print(sys.stdin.read()[:200])
"
