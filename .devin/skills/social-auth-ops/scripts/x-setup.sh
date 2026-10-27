#!/usr/bin/env bash
# x-setup.sh <client_id> <client_secret> [api_key] [api_secret]
# Writes X OAuth2 app creds to .env, then prints the reconnect URL to click.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
CID="${1:?usage: x-setup.sh <client_id> <client_secret> [api_key] [api_secret]}"
CSEC="${2:?client_secret required}"

set_kv() { grep -qE "^$1=" .env && sed -i "s|^$1=.*|$1=$2|" .env || echo "$1=$2" >> .env; }
set_kv TWITTER_CLIENT_ID "$CID"
set_kv TWITTER_CLIENT_SECRET "$CSEC"
[ -n "${3:-}" ] && set_kv TWITTER_API_KEY "$3"
[ -n "${4:-}" ] && set_kv TWITTER_API_SECRET "$4"
echo "creds written. Restarting api + publishing worker…"
docker compose restart social-api social-worker-publishing >/dev/null 2>&1 || true
sleep 8

EMAIL=$(grep -E '^SOCIAL_ADMIN_EMAIL=' .env | cut -d= -f2- | tr -d '\r')
PASS=$(grep -E '^SOCIAL_ADMIN_PASSWORD=' .env | cut -d= -f2- | tr -d '\r')
API="${SOCIAL_API:-http://localhost:8083}"
TOKEN=$(curl -s -X POST "$API/api/v1/auth/login" -H 'Content-Type: application/x-www-form-urlencoded' -d "username=$EMAIL&password=$PASS" | python3 -c "import json,sys;print(json.load(sys.stdin)['access_token'])")
TEAM=$(curl -s "$API/api/v1/teams" -H "Authorization: Bearer $TOKEN" | python3 -c "import json,sys;d=json.load(sys.stdin);print((d[0] if isinstance(d,list) else d.get('items',[{}])[0]).get('id',''))")
echo "Open this URL, approve, done:"
curl -s -X POST "$API/api/v1/accounts/connect/twitter?team_id=$TEAM" -H "Authorization: Bearer $TOKEN" | python3 -c "import json,sys;print(json.load(sys.stdin)['authorization_url'])"
