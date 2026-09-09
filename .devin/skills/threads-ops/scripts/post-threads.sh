#!/usr/bin/env bash
# Publish a simple text thread to the first connected Threads account.
set -euo pipefail

cd /home/tbaltzakis/cu130-slim

TEXT=${1:-""}
ACCOUNT_ID=${2:-""}
ADMIN_PASSWORD=$(grep "^SOCIAL_ADMIN_PASSWORD=" .env | cut -d= -f2)
API="http://localhost:8083"

if [ -z "$TEXT" ]; then
  echo "Usage: $0 \"Thread text\" [account_id]"
  exit 1
fi

echo "=== Logging in to SocialAuto ==="
TOKEN=$(curl -s -X POST "$API/api/v1/auth/login" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d "username=tbaltzakis@cloudless.gr&password=${ADMIN_PASSWORD}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))")

if [ -z "$TOKEN" ]; then
  echo "Could not log in to SocialAuto"
  exit 1
fi

# Resolve account ID if not provided
if [ -z "$ACCOUNT_ID" ]; then
  ACCOUNT_ID=$(curl -s -H "Authorization: Bearer $TOKEN" "$API/api/v1/accounts" | python3 -c "
import sys, json
data = json.load(sys.stdin)
accounts = data if isinstance(data, list) else data.get('accounts', [])
for a in accounts:
  if a.get('platform') == 'threads' and a.get('status') == 'active':
    print(a.get('id'))
    break
")
fi

if [ -z "$ACCOUNT_ID" ]; then
  echo "No active Threads account found. Connect one first:"
  echo "  bash .devin/skills/threads-ops/scripts/connect-threads.sh --open"
  exit 1
fi

echo "=== Posting to Threads account $ACCOUNT_ID ==="
curl -s -X POST "$API/api/v1/content" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"content\":\"$TEXT\",\"platform\":\"threads\",\"account_id\":\"$ACCOUNT_ID\",\"status\":\"published\"}" | python3 -m json.tool
