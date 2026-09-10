#!/usr/bin/env bash
# List Messenger conversations for a Facebook Page.
# Usage: list-conversations.sh [account_id] [--limit 25] [--platform messenger]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

ACCOUNT_ID="${1:-3f2f59c4-f190-44ad-aefe-4321af08ef89}"
shift 2>/dev/null || true

LIMIT=25
PLATFORM="messenger"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --limit) LIMIT="$2"; shift 2 ;;
    --platform) PLATFORM="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

API="${SOCIAL_API_URL:-http://127.0.0.1:8083}"
ADMIN_EMAIL=$(grep -E '^SOCIAL_ADMIN_EMAIL=' .env | cut -d= -f2-)
ADMIN_PASS=$(grep -E '^SOCIAL_ADMIN_PASSWORD=' .env | cut -d= -f2-)

TOKEN=$(curl -sf -X POST "$API/api/v1/auth/login" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=$ADMIN_EMAIL" \
  --data-urlencode "password=$ADMIN_PASS" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

curl -sf -H "Authorization: Bearer $TOKEN" \
  "$API/api/v1/messenger/$ACCOUNT_ID/conversations?limit=$LIMIT&platform=$PLATFORM" \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
if not data:
    print('No conversations found.')
    sys.exit(0)
for c in data:
    participants = c.get('participants') or []
    names = [p.get('name', p.get('id', '?')) for p in participants]
    print(f'ID:       {c.get(\"id\",\"?\")}')
    print(f'  People:  {\", \".join(names)}')
    print(f'  Snippet: {c.get(\"snippet\",\"\")}')
    print(f'  Updated: {c.get(\"updated_time\",\"?\")}')
    if c.get('unread_count'):
        print(f'  Unread:  {c[\"unread_count\"]}')
    print()
"
