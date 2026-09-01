#!/usr/bin/env bash
# Fetch current profile info for all connected social accounts.
# Shows what fields are readable and their current values.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"
source .env 2>/dev/null

TOKEN=$(curl -s -X POST http://127.0.0.1:8083/api/v1/auth/login \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d "username=${SOCIAL_ADMIN_EMAIL}&password=${SOCIAL_ADMIN_PASSWORD}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))")

if [ -z "$TOKEN" ]; then
  echo "Failed to authenticate" >&2
  exit 1
fi

echo "=== Connected Accounts ==="
curl -s http://127.0.0.1:8083/api/v1/accounts -H "Authorization: Bearer $TOKEN" \
  | python3 -c "
import sys, json
accounts = json.load(sys.stdin)
for a in accounts:
    print(f\"  {a['platform']:12}  id={a['id']}  name={a.get('display_name','')}  status={a.get('status','')}  type={a.get('account_type','')}\")
print(f\"\nTotal: {len(accounts)} accounts\")
"
