#!/usr/bin/env bash
# List all connected social accounts and their status.
# Usage: bash check-all-accounts.sh
set -euo pipefail

cd "$(dirname "$0")/../../.."

echo "=== Connected Social Accounts ==="
echo ""

# Check via database directly
docker compose exec -T social-api python3 -c "
import asyncio, os
from app.db.session import async_session
from sqlalchemy import select, text

async def main():
    async with async_session() as db:
        result = await db.execute(text('''
            SELECT platform, username, display_name, status, scopes, token_expires_at
            FROM social_accounts
            ORDER BY platform, created_at
        '''))
        rows = result.fetchall()
        if not rows:
            print('No accounts connected.')
            return
        for r in rows:
            platform, username, display_name, status, scopes, expires = r
            scopes_str = ', '.join(scopes) if scopes else 'none'
            print(f'  {platform:12s} | {username or \"unknown\":20s} | {status:8s} | scopes: {scopes_str}')
            if expires:
                print(f'               expires: {expires}')

asyncio.run(main())
" 2>&1
