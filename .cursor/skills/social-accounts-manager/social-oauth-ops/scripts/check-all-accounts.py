#!/usr/bin/env python3
"""List all connected social accounts and their status.
Usage: check-all-accounts.py"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import repo_root  # noqa: E402

QUERY = '''
import asyncio
from app.db.session import async_session_maker
from sqlalchemy import text

async def main():
    async with async_session_maker() as db:
        result = await db.execute(text("""
            SELECT platform, username, display_name, status, scopes, token_expires_at
            FROM social_accounts
            ORDER BY platform, created_at
        """))
        rows = result.fetchall()
        if not rows:
            print('No accounts connected.')
            return
        for r in rows:
            platform, username, display_name, status, scopes, expires = r
            scopes_str = ', '.join(scopes) if scopes else 'none'
            print(f'  {platform:12s} | {username or "unknown":20s} | {status:8s} | scopes: {scopes_str}')
            if expires:
                print(f'               expires: {expires}')

asyncio.run(main())
'''

print("=== Connected Social Accounts ===\n")
r = subprocess.run(
    ["docker", "compose", "exec", "-T", "social-api", "python3", "-c", QUERY],
    cwd=repo_root())
sys.exit(r.returncode)
