#!/usr/bin/env bash
# Update Facebook Page long description (Graph API)
# Usage: fb-page-update-description.sh "Long description text"
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

DESC_FILE="${1:?Usage: fb-page-update-description.sh <description>}"

# If argument is a file path, read from file; otherwise use as text
if [ -f "$DESC_FILE" ]; then
  DESC=$(cat "$DESC_FILE")
else
  DESC="$DESC_FILE"
fi

ACCOUNT_ID="ca22c266-4a93-47bd-b3ed-32b38d0ffa7b"

echo "→ Updating FB Page description (${#DESC} chars)..."
python3 << PYEOF
import asyncio
from app.core.security import decrypt_token
from app.db.session import engine
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

DESC = $(python3 -c "import json; print(json.dumps('''$DESC'''))")

async def main():
    async with AsyncSession(engine) as db:
        result = await db.execute(text("SELECT access_token_enc, account_id FROM social_accounts WHERE id='$ACCOUNT_ID'"))
        row = result.fetchone()
        token = decrypt_token(row[0])
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f'https://graph.facebook.com/v19.0/{row[1]}', params={'access_token': token}, data={'description': DESC})
            print(f'  Result: {r.status_code} - {r.text[:100]}')

asyncio.run(main())
PYEOF
