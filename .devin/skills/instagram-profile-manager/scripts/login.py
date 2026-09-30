#!/usr/bin/env python3
"""Login to Instagram sidecar using username and password.
Reads from social_secrets DB if not provided.
Usage: login.py [username] [password]"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import sc  # noqa: E402
from skill_http import repo_root  # noqa: E402

username = sys.argv[1] if len(sys.argv) > 1 else ""
password = sys.argv[2] if len(sys.argv) > 2 else ""

if not username or not password:
    out = subprocess.run(
        ["docker", "compose", "exec", "-T", "social-api", "python3", "-c", '''
import asyncio
from app.db.session import engine
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

async def main():
    async with AsyncSession(engine) as db:
        result = await db.execute(text("SELECT key, value FROM social_secrets WHERE key IN ('INSTAGRAM_USERNAME', 'INSTAGRAM_PASSWORD')"))
        creds = {row[0]: row[1] for row in result.fetchall()}
        print(f"{creds.get('INSTAGRAM_USERNAME','')}\\t{creds.get('INSTAGRAM_PASSWORD','')}")

asyncio.run(main())
'''], cwd=repo_root(), capture_output=True, text=True).stdout.strip().splitlines()
    if out:
        parts = out[-1].split("\t")
        username = username or (parts[0] if parts else "")
        password = password or (parts[1] if len(parts) > 1 else "")

if not username or not password:
    print("No Instagram credentials found. Provide username and password "
          "as args or store in social_secrets.")
    sys.exit(1)

print(f"Logging in to Instagram as {username}...")
d = sc("POST", "/auth/login", form={"username": username, "password": password})
if isinstance(d, dict):
    sid = d.get("session_id") or d.get("sessionid") or "N/A"
    print(f"Session ID: {sid}")
    if d.get("two_factor_required"):
        print("2FA required. Use login-2fa.py with verification code.")
    if d.get("challenge_required"):
        print("Challenge required. Use challenge-resolve.py with security code.")
    user = d.get("user", {})
    if user:
        print(f'Username: {user.get("username", "N/A")}')
else:
    print(d)
