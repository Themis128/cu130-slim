#!/usr/bin/env bash
# Update Instagram external URL (website link in bio)
# Usage: update-url.sh [session_id] "https://example.com"
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

API="${INSTAGRAM_SIDECAR_URL:-http://localhost:8011}"
SID="${1:?Usage: update-url.sh <session_id> \"https://example.com\"}"
URL="${2:?Usage: update-url.sh <session_id> \"https://example.com\"}"

echo "Updating Instagram external URL to $URL..."

curl -sf -X PATCH "$API/account/external-url" \
  -H "X-Session-ID: $SID" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "external_url=$URL" 2>&1 | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(f'Status: {d.get(\"status\", \"ok\")}')
    print(f'External URL: {d.get(\"external_url\", \"N/A\")}')
except:
    print(sys.stdin.read()[:200])
"
