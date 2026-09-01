#!/usr/bin/env bash
# Update Facebook Page about/description/website via the Graph API.
# Usage:
#   update-facebook-page.sh --about "New about" --description "New desc" --website "https://cloudless.gr"
# Requires: Facebook Page account connected with MANAGE task permission.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"
source .env 2>/dev/null

ABOUT=""
DESCRIPTION=""
WEBSITE=""
PHONE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --about) ABOUT="$2"; shift 2 ;;
    --description) DESCRIPTION="$2"; shift 2 ;;
    --website) WEBSITE="$2"; shift 2 ;;
    --phone) PHONE="$2"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [ -z "$ABOUT" ] && [ -z "$DESCRIPTION" ] && [ -z "$WEBSITE" ] && [ -z "$PHONE" ]; then
  echo "Usage: $0 --about \"text\" --description \"text\" --website \"url\" --phone \"number\"" >&2
  echo "At least one field must be provided." >&2
  exit 1
fi

TOKEN=$(curl -s -X POST http://127.0.0.1:8083/api/v1/auth/login \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d "username=${SOCIAL_ADMIN_EMAIL}&password=${SOCIAL_ADMIN_PASSWORD}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))")

# Find the Facebook Page account (type=page, is_business=true)
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

echo "Facebook Page account: $PAGE_ACCOUNT"

docker compose exec -T social-api python3 -c "
import asyncio, json, sys
from sqlalchemy import select
from app.db.session import async_session_maker
from app.models.social_account import SocialAccount
from app.core.security import decrypt_token
import httpx

ABOUT = sys.argv[1] if len(sys.argv) > 1 else ''
DESCRIPTION = sys.argv[2] if len(sys.argv) > 2 else ''
WEBSITE = sys.argv[3] if len(sys.argv) > 3 else ''
PHONE = sys.argv[4] if len(sys.argv) > 4 else ''
ACCOUNT_ID = sys.argv[5]

async def main():
    async with async_session_maker() as db:
        result = await db.execute(
            select(SocialAccount).where(SocialAccount.id == ACCOUNT_ID)
        )
        acct = result.scalar_one()
        token = decrypt_token(acct.access_token_enc)
        page_id = acct.account_id

        # Build update payload
        payload = {'access_token': token}
        if ABOUT: payload['about'] = ABOUT
        if DESCRIPTION: payload['description'] = DESCRIPTION
        if WEBSITE: payload['website'] = WEBSITE
        if PHONE: payload['phone'] = PHONE

        print(f'Updating Facebook Page {page_id} with: {list(k for k in payload if k != \"access_token\")}')

        async with httpx.AsyncClient(timeout=30) as client:
            # Get current profile for comparison
            r = await client.get(
                f'https://graph.facebook.com/v21.0/{page_id}',
                params={'fields': 'name,about,description,website,phone', 'access_token': token}
            )
            current = r.json()
            print(f'Current name: {current.get(\"name\",\"\")}')
            print(f'Current about: {current.get(\"about\",\"\")[:80]}')

            # Apply update
            r2 = await client.post(
                f'https://graph.facebook.com/v21.0/{page_id}',
                data=payload
            )
            resp = r2.json()
            if resp.get('success'):
                print('SUCCESS: Facebook Page profile updated')
            else:
                print(f'ERROR updating: {r2.status_code} {r2.text[:300]}')

asyncio.run(main())
" "$ABOUT" "$DESCRIPTION" "$WEBSITE" "$PHONE" "$PAGE_ACCOUNT" 2>&1 | grep -v "sqlalchemy\|INFO\|BEGIN\|ROLLBACK\|SELECT\|WHERE\|FROM"
