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
import json
d = {'content_text': '''$TEXT''', 'status': 'scheduled' if '''$SCHEDULED_AT''' else 'draft'}
if '''$SCHEDULED_AT''':
    d['scheduled_at'] = '''$SCHEDULED_AT'''
if '''$PLATFORM''':
    d['platforms'] = ['''$PLATFORM''']
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
"
