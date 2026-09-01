#!/usr/bin/env bash
# Publish a draft post immediately via SocialAuto API.
# Usage: publish-post.sh <post-id>
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

POST_ID="${1:?Usage: publish-post.sh <post-id>}"
API="${SOCIAL_API_URL:-http://127.0.0.1:8083}"

# Load env without sourcing (avoids .env parsing issues)
ADMIN_EMAIL=$(grep -E '^SOCIAL_ADMIN_EMAIL=' .env | cut -d= -f2-)
ADMIN_PASS=$(grep -E '^SOCIAL_ADMIN_PASSWORD=' .env | cut -d= -f2-)

TOKEN=$(curl -sf -X POST "$API/api/v1/auth/login" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=$ADMIN_EMAIL" \
  --data-urlencode "password=$ADMIN_PASS" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

echo "Publishing post $POST_ID..."
RESULT=$(curl -sf -X POST "$API/api/v1/content/posts/$POST_ID/publish-now" \
  -H "Authorization: Bearer $TOKEN")

echo "$RESULT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'Status: {d.get(\"status\",\"?\")}')
print(f'Scheduled at: {d.get(\"scheduled_at\",\"-\")}')
targets = d.get('targets', [])
for t in targets:
    print(f'  Target: {t.get(\"platform\",\"?\")} → {t.get(\"status\",\"?\")}  url={t.get(\"platform_url\",\"-\")}')
"
