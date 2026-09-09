#!/usr/bin/env bash
# Show brand sync status — which platforms are connected and what's synced.
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

echo "=== Brand Profile ==="
curl -sf "$API/api/v1/brand" \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
if d is None:
    print('No brand profile set.')
    sys.exit(0)
print(f'Name: {d.get(\"name\",\"?\")}')
print(f'Tagline: {d.get(\"tagline\",\"?\")}')
print(f'Website: {d.get(\"website_url\",\"?\")}')
v = d.get('visual') or {}
print(f'Logo: {v.get(\"logo_url\", \"not set\")}')
print(f'Primary color: {v.get(\"primary_color\", \"not set\")}')
print(f'Accent color: {v.get(\"accent_color\", \"not set\")}')
"

echo ""
echo "=== Connected Accounts ==="
curl -sf "$API/api/v1/accounts" \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
accounts = data if isinstance(data, list) else data.get('accounts', data.get('data', []))
platforms = {}
for a in accounts:
    p = a['platform']
    if p not in platforms:
        platforms[p] = []
    platforms[p].append(a)

for p in sorted(platforms.keys()):
    print(f'--- {p.upper()} ({len(platforms[p])}) ---')
    for a in platforms[p]:
        name = a.get('display_name') or a.get('username') or '(empty)'
        bio = (a.get('meta_data') or {}).get('biography', '')
        bio_short = bio[:60].replace(chr(10), ' / ') + '...' if len(bio) > 60 else bio.replace(chr(10), ' / ')
        avatar = 'set' if a.get('avatar_url') else 'MISSING'
        print(f'  {name:30} | avatar={avatar} | bio={bio_short}')
    print()
"
