#!/usr/bin/env bash
# List recent posts with optional filters.
# Usage: list-posts.sh [--status draft|scheduled|published|failed] [--limit 10] [--platform linkedin]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

API="${SOCIAL_API_URL:-http://127.0.0.1:8083}"
STATUS=""
LIMIT=10
PLATFORM=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --status) STATUS="$2"; shift 2 ;;
    --limit) LIMIT="$2"; shift 2 ;;
    --platform) PLATFORM="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

ADMIN_EMAIL=$(grep -E '^SOCIAL_ADMIN_EMAIL=' .env | cut -d= -f2-)
ADMIN_PASS=$(grep -E '^SOCIAL_ADMIN_PASSWORD=' .env | cut -d= -f2-)

TOKEN=$(curl -sf -X POST "$API/api/v1/auth/login" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=$ADMIN_EMAIL" \
  --data-urlencode "password=$ADMIN_PASS" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

PARAMS="limit=$LIMIT"
[[ -n "$STATUS" ]] && PARAMS="$PARAMS&status=$STATUS"
[[ -n "$PLATFORM" ]] && PARAMS="$PARAMS&platform=$PLATFORM"

curl -sf "http://127.0.0.1:8083/api/v1/content/posts?$PARAMS" \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
posts = d if isinstance(d, list) else d.get('posts', d.get('items', []))
if not posts:
    print('No posts found.')
for p in posts:
    status = p.get('status', '?')
    text = (p.get('content_text') or '')[:60].replace(chr(10), ' ')
    scheduled = p.get('scheduled_at', '-') or '-'
    targets = p.get('targets', [])
    urls = [t.get('platform_url','') for t in targets if t.get('platform_url')]
    url_str = urls[0] if urls else '-'
    print(f'{p[\"id\"]}  {status:10s}  {scheduled[:19]}  {text}  → {url_str}')
"
