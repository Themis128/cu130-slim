#!/usr/bin/env bash
# List all Messenger-capable accounts (Pages + personal)
set -euo pipefail
cd "$(dirname "$0")/../../.."

set -a
source .env 2>/dev/null || true
set +a
export SOCIAL_ADMIN_EMAIL SOCIAL_ADMIN_PASSWORD 2>/dev/null || true

TOKEN=$(curl -sf -X POST http://localhost:8083/api/v1/auth/login \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=${SOCIAL_ADMIN_EMAIL}" \
  --data-urlencode "password=${SOCIAL_ADMIN_PASSWORD}" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:8083/api/v1/accounts | python3 -c "
import sys, json
data = json.load(sys.stdin)
accounts = data.get('data', data) if isinstance(data, dict) else data
print('╔══════════════════════════════════════════════════════════╗')
print('║              Messenger-Capable Accounts                    ║')
print('╠══════════════════════════════════════════════════════════╣')
for a in accounts:
    if not isinstance(a, dict) or a.get('platform') != 'facebook':
        continue
    name = a.get('display_name') or a.get('username', 'Unknown')
    atype = a.get('account_type', 'unknown')
    aid = a.get('id', '')
    meta = a.get('meta_data', {})
    if atype == 'page':
        ms = meta.get('messenger_setup', {})
        sub = '✓ subscribed' if ms.get('subscribed') else '✗ not set up'
        print(f'║  📄 {name:<40} ║')
        print(f'║    Type: Page | {sub:<34} ║')
        print(f'║    ID: {aid:<44} ║')
    elif atype == 'user':
        browser = '✓ logged in' if meta.get('browser_storage_state') else '✗ needs login'
        print(f'║  👤 {name:<40} ║')
        print(f'║    Type: Personal | {browser:<31} ║')
        print(f'║    ID: {aid:<44} ║')
    print('╠══════════════════════════════════════════════════════════╣')
print('║ Use the Page ID for Messenger Platform API endpoints     ║')
print('║ Use the Personal ID for browser bridge endpoints         ║')
print('╚══════════════════════════════════════════════════════════╝')
"
