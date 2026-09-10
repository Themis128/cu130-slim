#!/usr/bin/env bash
# List personal Messenger conversations via browser bridge
# Usage: ./personal-conversations.sh <account_id>
set -euo pipefail
PROJECT_ROOT="/home/tbaltzakis/cu130-slim"

SOCIAL_ADMIN_EMAIL=$(grep -E "^SOCIAL_ADMIN_EMAIL=" "$PROJECT_ROOT/.env" | cut -d= -f2-)
SOCIAL_ADMIN_PASSWORD=$(grep -E "^SOCIAL_ADMIN_PASSWORD=" "$PROJECT_ROOT/.env" | cut -d= -f2-)

ACCOUNT_ID="${1:?Usage: $0 <account_id>}"

TOKEN=$(curl -sf -X POST http://localhost:8083/api/v1/auth/login \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=${SOCIAL_ADMIN_EMAIL}" \
  --data-urlencode "password=${SOCIAL_ADMIN_PASSWORD}" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

echo "Reading personal Messenger conversations (requires noVNC session)..."
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8083/api/v1/messenger/${ACCOUNT_ID}/personal/conversations" | python3 -m json.tool
