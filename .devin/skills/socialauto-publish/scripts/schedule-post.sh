#!/usr/bin/env bash
# Schedule a post for future publishing.
# Usage: schedule-post.sh <post-id> "2026-09-01T10:00:00Z"
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

POST_ID="${1:?Usage: schedule-post.sh <post-id> <iso-datetime>}"
SCHEDULED_AT="${2:?Usage: schedule-post.sh <post-id> <iso-datetime>}"
API="${SOCIAL_API_URL:-http://127.0.0.1:8083}"

ADMIN_EMAIL=$(grep -E '^SOCIAL_ADMIN_EMAIL=' .env | cut -d= -f2-)
ADMIN_PASS=$(grep -E '^SOCIAL_ADMIN_PASSWORD=' .env | cut -d= -f2-)

TOKEN=$(curl -sf -X POST "$API/api/v1/auth/login" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=$ADMIN_EMAIL" \
  --data-urlencode "password=$ADMIN_PASS" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

echo "Scheduling post $POST_ID for $SCHEDULED_AT..."
curl -sf -X POST "$API/api/v1/content/posts/$POST_ID/schedule?scheduled_at=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$SCHEDULED_AT'))")" \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'Status: {d.get(\"status\",\"?\")}')
print(f'Scheduled at: {d.get(\"scheduled_at\",\"-\")}')
"
