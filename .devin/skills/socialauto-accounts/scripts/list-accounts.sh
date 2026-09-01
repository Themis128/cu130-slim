#!/usr/bin/env bash
# List all connected social accounts.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

API="${SOCIAL_API_URL:-http://127.0.0.1:8083}"

ADMIN_EMAIL=$(grep -E '^SOCIAL_ADMIN_EMAIL=' .env | cut -d= -f2-)
ADMIN_PASS=$(grep -E '^SOCIAL_ADMIN_PASSWORD=' .env | cut -d= -f2-)

TOKEN=$(curl -sf -X POST "$API/api/v1/auth/login" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=$ADMIN_EMAIL" \
  --data-urlencode "password=$ADMIN_PASS" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

curl -sf "$API/api/v1/accounts" \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
items = d if isinstance(d, list) else d.get('accounts', d.get('items', []))
if not items:
    print('No accounts connected.')
for a in items:
    platform = a.get('platform', '?')
    name = a.get('display_name', '?')
    status = a.get('status', '?')
    atype = a.get('account_type', (a.get('meta_data') or {}).get('account_type', '?'))
    expires = a.get('token_expires_at', '-') or 'never'
    print(f'{a[\"id\"]}  {platform:10s}  {status:8s}  {atype:12s}  {name:30s}  expires: {expires[:19] if expires != \"never\" else \"never\"}')
"
