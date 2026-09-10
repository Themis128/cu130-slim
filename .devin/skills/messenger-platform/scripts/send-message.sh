#!/usr/bin/env bash
# Send a text message to a person on Messenger.
# Usage: send-message.sh <account_id> <recipient_psid> "message text"
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

ACCOUNT_ID="${1:?Usage: send-message.sh <account_id> <psid> \"text\"}"
PSID="${2:?Usage: send-message.sh <account_id> <psid> \"text\"}"
TEXT="${3:?Usage: send-message.sh <account_id> <psid> \"text\"}"

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
print(json.dumps({
    'recipient_psid': '$PSID',
    'text': '''$TEXT''',
    'messaging_type': 'RESPONSE'
}))
")

echo "Sending message to PSID $PSID..."
curl -sf -X POST "$API/api/v1/messenger/$ACCOUNT_ID/send" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d "$BODY" \
  | python3 -m json.tool
