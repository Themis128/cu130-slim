#!/usr/bin/env python3
"""Get Threads profile info via API and browser.
Usage: get-threads-profile.py [username]"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import api, threads_account  # noqa: E402
from skill_http import api_login, repo_root, request  # noqa: E402

base = api()
token = api_login(base)

username = sys.argv[1] if len(sys.argv) > 1 else ""
if not username:
    acct = threads_account(token)
    username = acct.get("username", "") if acct else ""
if not username:
    print("No Threads account connected.", file=sys.stderr)
    sys.exit(1)

print(f"=== Threads Profile: @{username} ===")
subprocess.run(
    ["docker", "compose", "exec", "-T", "social-api", "python", "-c", """
import asyncio
from app.db.session import get_db
from app.models.social_account import SocialAccount
from app.core.security import decrypt_token
from sqlalchemy import select
import httpx

async def check():
    async for db in get_db():
        result = await db.execute(select(SocialAccount).where(SocialAccount.platform == 'threads'))
        acc = result.scalar_one_or_none()
        if not acc:
            print('No Threads account in DB.')
            return
        token = decrypt_token(acc.access_token_enc)
        resp = await httpx.AsyncClient().get('https://graph.threads.net/v1.0/me', params={
            'fields': 'id,username,threads_profile_picture_url,threads_biography',
            'access_token': token,
        }, timeout=10)
        d = resp.json()
        print(f'Username: {d.get("username")}')
        print(f'ID: {d.get("id")}')
        print(f'Bio: {d.get("threads_biography", "")}')
        print(f'Profile pic: {"set" if d.get("threads_profile_picture_url") else "not set"}')
        print(f'Token expires: {acc.token_expires_at}')
        print(f'Display name (DB): {acc.display_name}')
asyncio.run(check())
"""], cwd=repo_root())
