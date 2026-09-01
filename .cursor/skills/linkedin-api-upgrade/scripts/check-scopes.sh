#!/usr/bin/env bash
# Check which LinkedIn scopes are currently configured and which accounts are connected.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"
source .env 2>/dev/null

echo "=== LinkedIn API Scope Configuration ==="
echo ""
echo "--- Requested OAuth Scopes ---"
grep -A 8 "^LINKEDIN_SCOPES" social-automation/backend/app/api/auth.py | head -10
echo ""

echo "--- Connected LinkedIn Accounts ---"
TOKEN=$(curl -s -X POST http://127.0.0.1:8083/api/v1/auth/login \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d "username=${SOCIAL_ADMIN_EMAIL}&password=${SOCIAL_ADMIN_PASSWORD}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))")

curl -s http://127.0.0.1:8083/api/v1/accounts -H "Authorization: Bearer $TOKEN" \
  | python3 -c "
import sys, json
accounts = json.load(sys.stdin)
for a in accounts:
    if a['platform'] == 'linkedin':
        print(f'  id={a[\"id\"]}  type={a.get(\"account_type\",\"\")}  name={a.get(\"display_name\",\"\")}  status={a.get(\"status\",\"\")}')
        print(f'    scopes: {a.get(\"scopes\",[])}')
print()
total = len([a for a in accounts if a['platform'] == 'linkedin'])
print(f'Total LinkedIn accounts: {total}')
print()
print('Note: Development tier allows max 5 members/pages/ad accounts.')
print('If you have more than 5 LinkedIn accounts, you need the standard tier upgrade.')
"
