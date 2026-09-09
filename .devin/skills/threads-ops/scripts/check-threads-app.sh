#!/usr/bin/env bash
# Check Threads app status and connected accounts.
set -euo pipefail

cd /home/tbaltzakis/cu130-slim

TEAM_ID="88e2bab4-3581-4c04-b0ac-87aa27840025"
ADMIN_PASSWORD=$(grep "^SOCIAL_ADMIN_PASSWORD=" .env | cut -d= -f2)
API="http://localhost:8083"

echo "=== Threads env configuration ==="
grep -E "^(THREADS|FACEBOOK)_(CLIENT_ID|CLIENT_SECRET|REDIRECT_URI)" .env | sed -E 's/(SECRET)=.*/\1=****/'

echo ""
echo "=== Threads OAuth URL ==="
TOKEN=$(curl -s -X POST "$API/api/v1/auth/login" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d "username=tbaltzakis@cloudless.gr&password=${ADMIN_PASSWORD}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))")

if [ -z "$TOKEN" ]; then
  echo "Could not log in to SocialAuto"
  exit 1
fi

curl -s -H "Authorization: Bearer $TOKEN" \
  "$API/api/v1/auth/oauth/threads/authorize?team_id=$TEAM_ID" | python3 -c "
import sys, json, urllib.parse
url = json.load(sys.stdin).get('authorization_url', '')
parsed = urllib.parse.urlparse(url)
qs = urllib.parse.parse_qs(parsed.query)
print(f'URL (truncated): {url[:120]}...')
print(f'Client ID: {qs.get(\"client_id\", [\"\"])[0]}')
print(f'Redirect URI: {qs.get(\"redirect_uri\", [\"\"])[0]}')
print(f'Scopes: {qs.get(\"scope\", [\"\"])[0]}')
"

echo ""
echo "=== Connected Threads accounts ==="
curl -s -H "Authorization: Bearer $TOKEN" "$API/api/v1/accounts" | python3 -c "
import sys, json
data = json.load(sys.stdin)
accounts = data if isinstance(data, list) else data.get('accounts', [])
threads = [a for a in accounts if a.get('platform') == 'threads']
if not threads:
  print('  No Threads accounts connected')
for a in threads:
  print(f\"  ID: {a.get('id')}\")
  print(f\"    Username: {a.get('username', '?')}\")
  print(f\"    Display: {a.get('display_name', '?')}\")
  print(f\"    Status: {a.get('status', '?')}\")
  print(f\"    Expires: {a.get('token_expires_at', '?')}\")
  print(f\"    Scopes: {a.get('scopes', [])}\")
"

echo ""
echo "=== Browser-novnc status ==="
curl -s http://localhost:9223/health | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"Status: {d.get('status')}, URL: {d.get('novnc_url')}\")"
