#!/usr/bin/env bash
# Update Instagram biography
# Usage: update-bio.sh [session_id] "new bio text"
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

API="${INSTAGRAM_SIDECAR_URL:-http://localhost:8011}"
SID="${1:?Usage: update-bio.sh <session_id> \"new bio text\"}"
BIO="${2:?Usage: update-bio.sh <session_id> \"new bio text\"}"

echo "Updating Instagram biography (${#BIO} chars)..."

curl -sf -X PATCH "$API/account/biography" \
  -H "X-Session-ID: $SID" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "biography=$BIO" 2>&1 | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(f'Status: {d.get(\"status\", \"ok\")}')
    print(f'Biography updated: {d.get(\"biography\", d.get(\"updated\", \"N/A\"))[:80]}')
except:
    print(sys.stdin.read()[:200])
"
