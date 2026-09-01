#!/usr/bin/env bash
# Update Facebook Page profile picture from a media library asset.
# Usage:
#   update-facebook-picture.sh <media_asset_id>
# Requires: Facebook Page account connected with MANAGE task permission.
# The media asset must have a publicly accessible URL (served via /api/v1/media/view).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"
source .env 2>/dev/null

MEDIA_ID="${1:-}"
if [ -z "$MEDIA_ID" ]; then
  echo "Usage: $0 <media_asset_id>" >&2
  exit 1
fi

TOKEN=$(curl -s -X POST http://127.0.0.1:8083/api/v1/auth/login \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d "username=${SOCIAL_ADMIN_EMAIL}&password=${SOCIAL_ADMIN_PASSWORD}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))")

# Find the Facebook Page account
PAGE_ACCOUNT=$(curl -s http://127.0.0.1:8083/api/v1/accounts -H "Authorization: Bearer $TOKEN" \
  | python3 -c "
import sys, json
accounts = json.load(sys.stdin)
for a in accounts:
    if a['platform'] == 'facebook' and a.get('is_business'):
        print(a['id'])
        break
")

if [ -z "$PAGE_ACCOUNT" ]; then
  echo "No Facebook Page (business) account found." >&2
  exit 1
fi

# Get the media asset's public URL
MEDIA_PUBLIC_BASE_URL="${MEDIA_PUBLIC_BASE_URL:-https://social.cloudless.gr}"
IMAGE_URL="${MEDIA_PUBLIC_BASE_URL}/api/v1/media/view?path=${MEDIA_ID}"

echo "Facebook Page account: $PAGE_ACCOUNT"
echo "Image URL: $IMAGE_URL"

docker compose exec -T social-api python3 -c "
import asyncio, sys
from sqlalchemy import select
from app.db.session import async_session_maker
from app.models.social_account import SocialAccount
from app.core.security import decrypt_token
import httpx

IMAGE_URL = sys.argv[1]
ACCOUNT_ID = sys.argv[2]

async def main():
    async with async_session_maker() as db:
        result = await db.execute(
            select(SocialAccount).where(SocialAccount.id == ACCOUNT_ID)
        )
        acct = result.scalar_one()
        token = decrypt_token(acct.access_token_enc)
        page_id = acct.account_id

        async with httpx.AsyncClient(timeout=60) as client:
            # Update profile picture via URL
            r = await client.post(
                f'https://graph.facebook.com/v21.0/{page_id}/picture',
                params={'url': IMAGE_URL, 'access_token': token}
            )
            resp = r.json()
            if resp.get('success'):
                print('SUCCESS: Facebook Page profile picture updated')
            else:
                print(f'ERROR: {r.status_code} {r.text[:300]}')

asyncio.run(main())
" "$IMAGE_URL" "$PAGE_ACCOUNT" 2>&1 | grep -v "sqlalchemy\|INFO\|BEGIN\|ROLLBACK\|SELECT\|WHERE\|FROM"
