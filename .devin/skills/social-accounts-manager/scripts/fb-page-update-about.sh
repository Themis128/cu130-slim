#!/usr/bin/env bash
# Update Facebook Page about (100 char limit)
# Usage: fb-page-update-about.sh "About text"
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

ABOUT="${1:?Usage: fb-page-update-about.sh <about>}"
ACCOUNT_ID="ca22c266-4a93-47bd-b3ed-32b38d0ffa7b"

if [ ${#ABOUT} -gt 100 ]; then
  echo "ERROR: About text exceeds 100 chars (${#ABOUT}). Use fb-page-update-description.sh for longer text."
  exit 1
fi

echo "→ Updating FB Page about (${#ABOUT} chars)..."
docker compose exec -T social-api python3 -c "
import asyncio
from app.core.security import decrypt_token
from app.db.session import engine
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

async def main():
    async with AsyncSession(engine) as db:
        result = await db.execute(text(\"SELECT access_token_enc, account_id FROM social_accounts WHERE id='$ACCOUNT_ID'\"))
        row = result.fetchone()
        token = decrypt_token(row[0])
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f'https://graph.facebook.com/v19.0/{row[1]}', params={'access_token': token}, data={'about': '''$ABOUT'''})
            print(f'  Result: {r.status_code} - {r.text[:100]}')

asyncio.run(main())
" 2>&1 | grep -v "INFO sqlalchemy"
