#!/usr/bin/env bash
# Read messages from a personal Messenger thread via browser bridge
# Usage: ./personal-read.sh <account_id> <thread_id>
set -euo pipefail
cd "$(dirname "$0")/../../.."

set -a
source .env 2>/dev/null || true
set +a

ACCOUNT_ID="${1:?Usage: $0 <account_id> <thread_id>}"
THREAD_ID="${2:?Usage: $0 <account_id> <thread_id>}"

TOKEN=$(curl -sf -X POST http://localhost:8083/api/v1/auth/login \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=${SOCIAL_ADMIN_EMAIL}" \
  --data-urlencode "password=${SOCIAL_ADMIN_PASSWORD}" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

echo "Reading personal Messenger thread (requires noVNC session)..."
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8083/api/v1/messenger/${ACCOUNT_ID}/personal/conversations/${THREAD_ID}" | python3 -m json.tool
