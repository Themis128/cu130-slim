#!/usr/bin/env bash
# Get messages in a conversation thread.
# Usage: get-messages.sh <account_id> <conversation_id> [--limit 20]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

ACCOUNT_ID="${1:?Usage: get-messages.sh <account_id> <conversation_id> [--limit N]}"
CONV_ID="${2:?Usage: get-messages.sh <account_id> <conversation_id> [--limit N]}"
shift 2

LIMIT=20
while [[ $# -gt 0 ]]; do
  case "$1" in
    --limit) LIMIT="$2"; shift 2 ;;
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
  "$API/api/v1/messenger/$ACCOUNT_ID/conversations/$CONV_ID?limit=$LIMIT" \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
if not data:
    print('No messages found.')
    sys.exit(0)
for m in data:
    sender = m.get('from_id', '?')
    text = m.get('message', '')
    ts = m.get('created_time', '?')
    print(f'[{ts}] {sender}: {text}')
"
