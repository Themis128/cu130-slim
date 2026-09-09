#!/usr/bin/env bash
# Get Threads profile info via API and browser.
# Usage: get-threads-profile.sh [username]
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

USERNAME="${1:-}"

if [ -z "$USERNAME" ]; then
  # Get username from connected accounts
  USERNAME=$(curl -sf "$API/api/v1/accounts" \
    -H "Authorization: Bearer $TOKEN" \
    | python3 -c "
import sys, json
data = json.load(sys.stdin)
accounts = data if isinstance(data, list) else data.get('accounts', data.get('data', []))
for a in accounts:
    if a['platform'] == 'threads':
        print(a.get('username', ''))
        break
")
fi

if [ -z "$USERNAME" ]; then
  echo "No Threads account connected."
  exit 1
fi

echo "=== Threads Profile: @$USERNAME ==="

# Get profile via Threads API
docker compose exec -T social-api python -c "
import asyncio
from app.db.session import get_db
from app.models.social_account import SocialAccount
from app.core.security import decrypt_token
from sqlalchemy import select
import httpx

async def check():
    async for db in get_db():
        result = await db.execute(select(SocialAccount).where(SocialAccount.platform == 'threads'))
        acc = result.scalar_one_or_none()
        if not acc:
            print('No Threads account in DB.')
            return
        token = decrypt_token(acc.access_token_enc)
        resp = await httpx.AsyncClient().get('https://graph.threads.net/v1.0/me', params={
            'fields': 'id,username,threads_profile_picture_url,threads_biography',
            'access_token': token,
        }, timeout=10)
        d = resp.json()
        print(f'Username: {d.get(\"username\")}')
        print(f'ID: {d.get(\"id\")}')
        print(f'Bio: {d.get(\"threads_biography\", \"\")}')
        print(f'Profile pic: {\"set\" if d.get(\"threads_profile_picture_url\") else \"not set\"}')
        print(f'Token expires: {acc.token_expires_at}')
        print(f'Display name (DB): {acc.display_name}')
asyncio.run(check())
" 2>&1
