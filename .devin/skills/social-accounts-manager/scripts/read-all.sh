#!/usr/bin/env bash
# Read all connected social profiles and show current state
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

TOKEN=$(curl -s -X POST http://localhost:8083/api/v1/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=tbaltzakis@cloudless.gr&password=TH!123789th!" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

echo "=== All Connected Accounts ==="
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8083/api/v1/accounts | python3 -c "
import sys, json
for a in json.load(sys.stdin):
    print(f'  {a[\"platform\"]:12s} | {a[\"account_type\"]:12s} | {a[\"display_name\"]:30s} | {a[\"status\"]:8s} | {a[\"id\"]}')
"

echo ""
echo "=== Facebook Page (cloudless.gr) ==="
docker compose exec -T social-api python3 -c "
import asyncio
from app.core.security import decrypt_token
from app.db.session import engine
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

async def main():
    async with AsyncSession(engine) as db:
        result = await db.execute(text(\"SELECT access_token_enc, account_id FROM social_accounts WHERE id='ca22c266-4a93-47bd-b3ed-32b38d0ffa7b'\"))
        row = result.fetchone()
        token = decrypt_token(row[0])
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f'https://graph.facebook.com/v19.0/{row[1]}', params={'access_token': token, 'fields': 'name,about,description,website,phone'})
            d = r.json()
            print(f'  Name: {d.get(\"name\")}')
            print(f'  About: {d.get(\"about\",\"\")[:80]}')
            print(f'  Website: {d.get(\"website\")}')
            print(f'  Phone: {d.get(\"phone\")}')
            desc = d.get('description','')
            has_cloud = 'cloudless.gr' in desc
            has_portfolio = 'baltzakisthemis' in desc
            has_wa = 'wa.me' in desc
            print(f'  Description has cloudless.gr: {has_cloud}')
            print(f'  Description has portfolio: {has_portfolio}')
            print(f'  Description has WhatsApp: {has_wa}')

asyncio.run(main())
" 2>&1 | grep -v "INFO sqlalchemy"

echo ""
echo "=== LinkedIn Organization (cloudless.gr) ==="
curl -s http://localhost:9225/company/cloudless-gr 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    c = d.get('company', {})
    print(f'  Name: {c.get(\"name\")}')
    print(f'  About: {c.get(\"about\",\"\")[:80]}')
    print(f'  Website: {c.get(\"website\")}')
    print(f'  Specialties: {c.get(\"specialties\")}')
except: print('  (sidecar not available)')
"

echo ""
echo "=== LinkedIn Personal ==="
curl -s http://localhost:9225/profile 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    p = d.get('profile', d)
    print(f'  Headline: {p.get(\"headline\")}')
    about = p.get('about','')
    print(f'  About: {about[:80]}')
    print(f'  Website: {p.get(\"website\")}')
    has_cloud = 'cloudless.gr' in about
    has_portfolio = 'baltzakisthemis' in about
    print(f'  About has cloudless.gr: {has_cloud}')
    print(f'  About has portfolio: {has_portfolio}')
except: print('  (sidecar not available)')
"

echo ""
echo "=== Instagram ==="
curl -s http://localhost:8011/account 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(f'  Username: {d.get(\"username\")}')
    print(f'  Biography: {d.get(\"biography\",\"\")[:80]}')
    print(f'  External URL: {d.get(\"external_url\")}')
except: print('  (sidecar not logged in)')
"
