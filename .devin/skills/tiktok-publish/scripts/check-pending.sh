#!/usr/bin/env bash
# Check the status of recent TikTok uploads by querying the SocialAuto API
# for recent TikTok post targets and their publish status.
# Also extracts known publish_ids from worker logs and checks their status.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

API="${SOCIAL_API_URL:-http://127.0.0.1:8083}"

ADMIN_EMAIL=$(grep -E '^SOCIAL_ADMIN_EMAIL=' .env | cut -d= -f2-)
ADMIN_PASS=$(grep -E '^SOCIAL_ADMIN_PASSWORD=' .env | cut -d= -f2-)

TOKEN=$(curl -sf -X POST "$API/api/v1/auth/login" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=$ADMIN_EMAIL" \
  --data-urlencode "password=$ADMIN_PASS" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

echo "=== Recent TikTok post targets ==="
curl -sf -H "Authorization: Bearer $TOKEN" "$API/api/v1/content/posts?limit=30" | python3 -c "
import sys, json
d = json.load(sys.stdin)
posts = d.get('posts', [])
pending = 0
failed = 0
published = 0
for p in posts:
    for t in p.get('targets', []):
        if t.get('platform') != 'tiktok':
            continue
        status = t.get('status', '?')
        pid = t.get('platform_post_id') or '-'
        if status == 'pending': pending += 1
        elif status == 'failed': failed += 1
        elif status == 'published': published += 1
        print(f'  post={p[\"id\"][:12]}  target={status:10s}  pid={pid[:40] if pid != \"-\" else \"-\"}')
print(f'\\nSummary: {pending} pending, {failed} failed, {published} published')
if pending >= 4:
    print('\\nWARNING: 4+ pending TikTok targets. Risk of spam_risk_too_many_pending_share.')
    print('Clear pending uploads from the TikTok mobile app or cancel via cancel-upload.sh.')
"

echo ""
echo "=== Known upload_ids from worker logs ==="
UPLOAD_IDS=$(docker compose logs social-worker-publishing 2>&1 | grep -oP 'upload_id=\d+' | grep -oP '\d+' | sort -u)
if [ -z "$UPLOAD_IDS" ]; then
    echo "No upload_ids found in logs."
else
    echo "$UPLOAD_IDS" | while read -r UID; do
        echo "  upload_id=$UID  (publish_id=v_inbox_file~v2.$UID)"
    done
    echo ""
    echo "To cancel: .devin/skills/tiktok-publish/scripts/cancel-upload.sh v_inbox_file~v2.<upload_id>"
fi
