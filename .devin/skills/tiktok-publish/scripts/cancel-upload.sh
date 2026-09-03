#!/usr/bin/env bash
# Cancel a pending TikTok upload by publish_id.
# Usage: cancel-upload.sh <publish_id>
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

PUBLISH_ID="${1:?Usage: cancel-upload.sh <publish_id>}"

docker compose exec -T social-api python3 -c "
import asyncio, os
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from app.core.security import decrypt_token
import httpx

DB_URL = os.environ.get('DATABASE_URL','').replace('postgresql://','postgresql+asyncpg://')
engine = create_async_engine(DB_URL)

async def main():
    async with engine.begin() as conn:
        result = await conn.execute(text(\"SELECT access_token_enc FROM social_accounts WHERE platform = 'tiktok' LIMIT 1\"))
        row = result.first()
        if not row:
            print('No TikTok account found')
            return
        enc = row[0]
        if isinstance(enc, str):
            enc = bytes.fromhex(enc)
        token = decrypt_token(enc)

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            'https://open.tiktokapis.com/v2/post/publish/cancel/',
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
            json={'publish_id': '${PUBLISH_ID}'}
        )
        data = resp.json()
        code = data.get('error',{}).get('code','?')
        msg = data.get('error',{}).get('message','')
        print(f'Cancel ${PUBLISH_ID}: HTTP {resp.status_code} code={code} {msg}')

asyncio.run(main())
"
