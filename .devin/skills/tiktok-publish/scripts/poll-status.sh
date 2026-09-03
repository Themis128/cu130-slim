#!/usr/bin/env bash
# Poll a SocialAuto post's TikTok publish status until complete or failed.
# Usage: poll-status.sh <post-id> [max-wait-seconds]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

POST_ID="${1:?Usage: poll-status.sh <post-id> [max-wait-seconds]}"
MAX_WAIT="${2:-180}"

API="${SOCIAL_API_URL:-http://127.0.0.1:8083}"

ADMIN_EMAIL=$(grep -E '^SOCIAL_ADMIN_EMAIL=' .env | cut -d= -f2-)
ADMIN_PASS=$(grep -E '^SOCIAL_ADMIN_PASSWORD=' .env | cut -d= -f2-)

TOKEN=$(curl -sf -X POST "$API/api/v1/auth/login" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=$ADMIN_EMAIL" \
  --data-urlencode "password=$ADMIN_PASS" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

INTERVAL=10
ELAPSED=0
while [ "$ELAPSED" -lt "$MAX_WAIT" ]; do
    D=$(curl -s -H "Authorization: Bearer $TOKEN" "$API/api/v1/content/posts/$POST_ID")
    STATUS=$(echo "$D" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('status','?'))")
    TGT=$(echo "$D" | python3 -c "import sys,json;d=json.load(sys.stdin);t=d.get('targets',[]);print([(x['platform'],x['status'],x.get('platform_post_id'),x.get('platform_url')) for x in t])")
    REASON=$(echo "$D" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('failure_reason') or '')")
    echo "[${ELAPSED}s] post=$STATUS targets=$TGT reason=$REASON"
    if [ "$STATUS" = "published" ] || [ "$STATUS" = "failed" ]; then
        exit 0
    fi
    sleep "$INTERVAL"
    ELAPSED=$((ELAPSED + INTERVAL))
done
echo "Timeout after ${MAX_WAIT}s — post is still $STATUS"
exit 1
