#!/usr/bin/env bash
# Create a quick text-only post (draft or scheduled).
# Usage: create-post.sh "Your post text" [--platform linkedin] [--schedule "2026-09-01T10:00:00Z"]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

TEXT="${1:?Usage: create-post.sh \"text\" [--platform ...] [--schedule ...]}"
shift

PLATFORM=""
SCHEDULED_AT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --platform) PLATFORM="$2"; shift 2 ;;
    --schedule) SCHEDULED_AT="$2"; shift 2 ;;
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

BODY=$(python3 -c "
import json, urllib.request

token = '''$TOKEN'''
api = '''$API'''
platform = '''$PLATFORM'''
scheduled_at = '''$SCHEDULED_AT'''
text = '''$TEXT'''

d = {'content_text': text, 'status': 'scheduled' if scheduled_at else 'draft'}
if scheduled_at:
    d['scheduled_at'] = scheduled_at

if platform:
    # Resolve platform name to account IDs
    req = urllib.request.Request(f'{api}/api/v1/accounts',
        headers={'Authorization': f'Bearer {token}'})
    accounts = json.loads(urllib.request.urlopen(req).read())
    if isinstance(accounts, dict):
        accounts = accounts.get('accounts', accounts.get('items', []))
    ids = [a['id'] for a in accounts if a.get('platform') == platform and a.get('status') == 'active']
    if not ids:
        raise SystemExit(f'No active account found for platform: {platform}')
    d['target_account_ids'] = ids

print(json.dumps(d))
")

echo "Creating post..."
curl -sf -X POST "$API/api/v1/content/posts" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d "$BODY" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'Post ID: {d.get(\"id\",\"?\")}')
print(f'Status: {d.get(\"status\",\"?\")}')
print(f'Scheduled: {d.get(\"scheduled_at\",\"-\")}')
print(f'Targets: {len(d.get(\"targets\",[]))}')
"
