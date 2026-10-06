#!/usr/bin/env python3
"""Cancel a pending TikTok upload by publish_id.
Usage: cancel-upload.py <publish_id>"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import repo_root, usage  # noqa: E402

publish_id = sys.argv[1] if len(sys.argv) > 1 else \
    usage("cancel-upload.py <publish_id>")

CANCEL_PY = f'''
import asyncio, os
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from app.core.security import decrypt_token
import httpx

DB_URL = os.environ.get("DATABASE_URL", "").replace("postgresql://", "postgresql+asyncpg://")
engine = create_async_engine(DB_URL)

async def main():
    async with engine.begin() as conn:
        result = await conn.execute(text(
            "SELECT access_token_enc FROM social_accounts WHERE platform = 'tiktok' LIMIT 1"))
        row = result.first()
        if not row:
            print("No TikTok account found")
            return
        enc = row[0]
        if isinstance(enc, str):
            enc = bytes.fromhex(enc)
        token = decrypt_token(enc)

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://open.tiktokapis.com/v2/post/publish/cancel/",
            headers={{"Authorization": f"Bearer {{token}}", "Content-Type": "application/json"}},
            json={{"publish_id": {publish_id!r}}},
        )
        data = resp.json()
        code = data.get("error", {{}}).get("code", "?")
        msg = data.get("error", {{}}).get("message", "")
        print(f"Cancel {publish_id}: HTTP {{resp.status_code}} code={{code}} {{msg}}")

asyncio.run(main())
'''

sys.exit(subprocess.run(
    ["docker", "compose", "exec", "-T", "social-api",
     "python3", "-c", CANCEL_PY], cwd=repo_root()).returncode)
