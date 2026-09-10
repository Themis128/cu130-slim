#!/usr/bin/env bash
# Get or set AI auto-reply config for a Page
# Usage: ./page-auto-reply.sh <account_id> [get|set] [enabled] [system_prompt]
set -euo pipefail
cd "$(dirname "$0")/../../.."

set -a
source .env 2>/dev/null || true
set +a

ACCOUNT_ID="${1:?Usage: $0 <account_id> [get|set] [enabled] [system_prompt]}"
ACTION="${2:-get}"

TOKEN=$(curl -sf -X POST http://localhost:8083/api/v1/auth/login \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=${SOCIAL_ADMIN_EMAIL}" \
  --data-urlencode "password=${SOCIAL_ADMIN_PASSWORD}" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

if [ "$ACTION" = "get" ]; then
  curl -s -H "Authorization: Bearer $TOKEN" \
    "http://localhost:8083/api/v1/messenger/${ACCOUNT_ID}/auto-reply" | python3 -m json.tool
elif [ "$ACTION" = "set" ]; then
  ENABLED="${3:?Usage: $0 <account_id> set <true|false> [system_prompt]}"
  PROMPT="${4:-You are a helpful assistant for {page_name}. Reply concisely.}"
  curl -s -X PUT -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"enabled\": $ENABLED, \"system_prompt\": \"$PROMPT\"}" \
    "http://localhost:8083/api/v1/messenger/${ACCOUNT_ID}/auto-reply" | python3 -m json.tool
else
  echo "Unknown action: $ACTION (use 'get' or 'set')"
  exit 1
fi
