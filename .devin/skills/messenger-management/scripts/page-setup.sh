#!/usr/bin/env bash
# Set up Messenger on a Facebook Page
# Usage: ./page-setup.sh <account_id> [greeting_text]
set -euo pipefail
cd "$(dirname "$0")/../../.."

set -a
source .env 2>/dev/null || true
set +a

ACCOUNT_ID="${1:?Usage: $0 <account_id> [greeting_text]}"
GREETING="${2:-}"

TOKEN=$(curl -sf -X POST http://localhost:8083/api/v1/auth/login \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=${SOCIAL_ADMIN_EMAIL}" \
  --data-urlencode "password=${SOCIAL_ADMIN_PASSWORD}" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

BODY="{}"
if [ -n "$GREETING" ]; then
  BODY="{\"greeting_text\": \"$GREETING\"}"
fi

echo "Setting up Messenger on account $ACCOUNT_ID..."
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "$BODY" \
  "http://localhost:8083/api/v1/messenger/${ACCOUNT_ID}/setup" | python3 -m json.tool
