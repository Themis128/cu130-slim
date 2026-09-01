#!/usr/bin/env bash
# Update LinkedIn Company Page description and/or about via the Organizations API.
# Usage:
#   update-linkedin-org.sh "New description" "New about/specialties"
# Requires: LinkedIn org account connected with rw_organization_admin scope.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"
source .env 2>/dev/null

DESCRIPTION="${1:-}"
ABOUT="${2:-}"

if [ -z "$DESCRIPTION" ] && [ -z "$ABOUT" ]; then
  echo "Usage: $0 \"description text\" \"about/specialties text\"" >&2
  echo "At least one of description or about must be provided." >&2
  exit 1
fi

TOKEN=$(curl -s -X POST http://127.0.0.1:8083/api/v1/auth/login \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d "username=${SOCIAL_ADMIN_EMAIL}&password=${SOCIAL_ADMIN_PASSWORD}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))")

# Find the LinkedIn org account
ORG_ACCOUNT=$(curl -s http://127.0.0.1:8083/api/v1/accounts -H "Authorization: Bearer $TOKEN" \
  | python3 -c "
import sys, json
accounts = json.load(sys.stdin)
for a in accounts:
    if a['platform'] == 'linkedin' and a.get('account_type') == 'organization':
        print(a['id'])
        break
")

if [ -z "$ORG_ACCOUNT" ]; then
  echo "No LinkedIn organization account found." >&2
  exit 1
fi

echo "LinkedIn org account: $ORG_ACCOUNT"
echo "Updating profile via social-api..."

# Use the social-api container to make the LinkedIn API call
docker compose exec -T social-api python3 -c "
import asyncio, json, sys
from sqlalchemy import select
from app.db.session import async_session_maker
from app.models.social_account import SocialAccount
from app.core.security import decrypt_token
import httpx

DESCRIPTION = sys.argv[1] if len(sys.argv) > 1 else ''
ABOUT = sys.argv[2] if len(sys.argv) > 2 else ''
ACCOUNT_ID = sys.argv[3]

async def main():
    async with async_session_maker() as db:
        result = await db.execute(
            select(SocialAccount).where(SocialAccount.id == ACCOUNT_ID)
        )
        acct = result.scalar_one()
        token = decrypt_token(acct.access_token_enc)
        org_id = acct.account_id

        # Build patch payload
        patch = {}
        if DESCRIPTION:
            patch['description'] = {'value': DESCRIPTION}
        if ABOUT:
            patch['specialties'] = {'values': [{'value': ABOUT}]}

        print(f'Patching org {org_id} with: {json.dumps(list(patch.keys()))}')

        async with httpx.AsyncClient(timeout=30) as client:
            # First, get current profile for comparison
            r = await client.get(
                f'https://api.linkedin.com/rest/organizations/{org_id}',
                headers={
                    'Authorization': f'Bearer {token}',
                    'LinkedIn-Version': '202501',
                },
                params={'projection': '(id,localizedName,localizedDescription,localizedSpecialties)'}
            )
            if r.status_code != 200:
                print(f'ERROR reading profile: {r.status_code} {r.text[:300]}')
                return
            current = r.json()
            print(f'Current name: {current.get(\"localizedName\",\"\")}')
            print(f'Current description: {current.get(\"localizedDescription\",\"\")[:100]}...')

            # Apply update
            r2 = await client.post(
                f'https://api.linkedin.com/rest/organizations/{org_id}',
                headers={
                    'Authorization': f'Bearer {token}',
                    'Content-Type': 'application/json',
                    'X-Restli-Method': 'PARTIAL_UPDATE',
                    'LinkedIn-Version': '202501',
                },
                json=patch
            )
            if r2.status_code in (200, 204):
                print('SUCCESS: LinkedIn org profile updated')
            else:
                print(f'ERROR updating: {r2.status_code} {r2.text[:300]}')

asyncio.run(main())
" "$DESCRIPTION" "$ABOUT" "$ORG_ACCOUNT" 2>&1 | grep -v "sqlalchemy\|INFO\|BEGIN\|ROLLBACK\|SELECT\|WHERE\|FROM"
