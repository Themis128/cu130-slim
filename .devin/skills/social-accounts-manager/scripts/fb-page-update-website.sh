#!/usr/bin/env bash
# Update Facebook Page website (Graph API)
# Usage: fb-page-update-website.sh "https://cloudless.gr"
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

URL="${1:?Usage: fb-page-update-website.sh <url>}"
ACCOUNT_ID="ca22c266-4a93-47bd-b3ed-32b38d0ffa7b"

echo "→ Updating FB Page website to $URL..."
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
            r = await c.post(f'https://graph.facebook.com/v19.0/{row[1]}', params={'access_token': token}, data={'website': '$URL'})
            print(f'  Result: {r.status_code} - {r.text[:100]}')

asyncio.run(main())
" 2>&1 | grep -v "INFO sqlalchemy"
