#!/usr/bin/env bash
# Send a Page Messenger message
# Usage: ./page-send.sh <account_id> <recipient_psid> <text>
set -euo pipefail
PROJECT_ROOT="/home/tbaltzakis/cu130-slim"

SOCIAL_ADMIN_EMAIL=$(grep -E "^SOCIAL_ADMIN_EMAIL=" "$PROJECT_ROOT/.env" | cut -d= -f2-)
SOCIAL_ADMIN_PASSWORD=$(grep -E "^SOCIAL_ADMIN_PASSWORD=" "$PROJECT_ROOT/.env" | cut -d= -f2-)

ACCOUNT_ID="${1:?Usage: $0 <account_id> <recipient_psid> <text>}"
PSID="${2:?Usage: $0 <account_id> <recipient_psid> <text>}"
TEXT="${3:?Usage: $0 <account_id> <recipient_psid> <text>}"

TOKEN=$(curl -sf -X POST http://localhost:8083/api/v1/auth/login \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=${SOCIAL_ADMIN_EMAIL}" \
  --data-urlencode "password=${SOCIAL_ADMIN_PASSWORD}" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"recipient_psid\": \"$PSID\", \"text\": \"$TEXT\"}" \
  "http://localhost:8083/api/v1/messenger/${ACCOUNT_ID}/send" | python3 -m json.tool
