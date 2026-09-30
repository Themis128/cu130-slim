#!/usr/bin/env python3
"""Shared FB Page field update via Graph API (runs inside social-api container)."""

import json
import sys
from pathlib import Path

from _common import FB_PAGE_ACCOUNT_ID, api_py


def update_field(field: str, val: str, max_len: int | None = None) -> None:
    if max_len and len(val) > max_len:
        print(f"ERROR: text exceeds {max_len} chars ({len(val)}).")
        sys.exit(1)
    print(f"→ Updating FB Page {field} ({len(val)} chars)...")
    out = api_py(f"""
import asyncio
from app.core.security import decrypt_token
from app.db.session import engine
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

VAL = {val!r}

async def main():
    async with AsyncSession(engine) as db:
        result = await db.execute(text("SELECT access_token_enc, account_id FROM social_accounts WHERE id='{FB_PAGE_ACCOUNT_ID}'"))
        row = result.fetchone()
        token = decrypt_token(row[0])
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f'https://graph.facebook.com/v19.0/{{row[1]}}', params={{'access_token': token}}, data={{'{field}': VAL}})
            print(f'  Result: {{r.status_code}} - {{r.text[:100]}}')

asyncio.run(main())
""")
    print(out)
