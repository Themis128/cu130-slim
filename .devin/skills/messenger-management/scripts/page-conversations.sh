#!/usr/bin/env bash
# List Page Messenger conversations
# Usage: ./page-conversations.sh <account_id> [limit]
set -euo pipefail
cd "$(dirname "$0")/../../.."

source .env 2>/dev/null || true

ACCOUNT_ID="${1:?Usage: $0 <account_id> [limit]}"
LIMIT="${2:-25}"

TOKEN=$(curl -sf -X POST http://localhost:8083/api/v1/auth/login \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=${SOCIAL_ADMIN_EMAIL}" \
  --data-urlencode "password=${SOCIAL_ADMIN_PASSWORD}" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

curl -s -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8083/api/v1/messenger/${ACCOUNT_ID}/conversations?limit=${LIMIT}" | python3 -m json.tool
