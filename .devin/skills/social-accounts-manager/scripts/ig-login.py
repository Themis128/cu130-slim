#!/usr/bin/env python3
"""Login to Instagram sidecar (tries saved sessionids, then username/password).
Usage: ig-login.py"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import IG_API, IG_SESSION_FILE, api_py, http  # noqa: E402
from skill_http import repo_root  # noqa: E402

print("→ Attempting Instagram login...", file=sys.stderr)

# Try saved sessions first
out = subprocess.run(
    ["docker", "compose", "exec", "-T", "instagram-private-api", "python3", "-c", """
import json
with open('/data/db.json') as f:
    d = json.load(f)
sessions = d.get('_default', {})
if isinstance(sessions, dict):
    for k, v in sorted(sessions.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 0, reverse=True):
        if isinstance(v, dict) and 'sessionid' in v:
            sid = v['sessionid']
            if sid.startswith('36910035385'):
                print(sid)
"""], cwd=repo_root(), capture_output=True, text=True).stdout

sids = [s.strip() for s in out.splitlines() if s.strip()]
if sids:
    print("  Found saved sessions, trying them...", file=sys.stderr)
    for sid in sids:
        print(f"  Trying: {sid[:30]}...", file=sys.stderr)
        resp = http("POST", f"{IG_API}/auth/login/by/sessionid",
                    form={"sessionid": sid})
        try:
            d = json.loads(resp)
            sidecar_sid = d.get("session_id") or d.get("sessionid") or ""
        except ValueError:
            sidecar_sid = ""
        if sidecar_sid:
            IG_SESSION_FILE.write_text(sidecar_sid)
            print(f"  ✓ Login successful! Session: {sidecar_sid[:30]}...", file=sys.stderr)
            sys.exit(0)

# Try username/password
print("  No saved sessions worked, trying username/password...", file=sys.stderr)
creds = api_py('''
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
''').strip().splitlines()
username = password = ""
if creds:
    parts = creds[-1].split("\t")
    username = parts[0] if parts else ""
    password = parts[1] if len(parts) > 1 else ""

if not username or not password:
    print("  ERROR: No Instagram credentials found in secret store.", file=sys.stderr)
    sys.exit(1)

resp = http("POST", f"{IG_API}/auth/login", form={
    "username": username, "password": password,
    "locale": "el_GR", "timezone": "10800",
    "proxy": "socks5://warp-proxy:1080"})
try:
    sidecar_sid = json.loads(resp).get("session_id") or ""
except ValueError:
    sidecar_sid = ""

if sidecar_sid:
    IG_SESSION_FILE.write_text(sidecar_sid)
    print(f"  ✓ Login successful! Session: {sidecar_sid[:30]}...", file=sys.stderr)
else:
    print(f"  ✗ Login failed: {resp[:200]}", file=sys.stderr)
    print("  Try: python3 .devin/skills/instagram-profile-manager/scripts/login-via-facebook.py",
          file=sys.stderr)
    sys.exit(1)
