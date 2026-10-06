#!/usr/bin/env python3
"""Read all connected social profiles and show current state."""

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import FB_PAGE_ACCOUNT_ID, IG_API, LI_SIDECAR, api_py  # noqa: E402
from skill_http import request, social_api  # noqa: E402


def get(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return json.loads(r.read().decode())
    except Exception:
        return {}


api, token = social_api()

print("=== All Connected Accounts ===")
accounts = request("GET", f"{api}/api/v1/accounts", token=token)
for a in (accounts if isinstance(accounts, list) else accounts.get("accounts", [])):
    print(f'  {a["platform"]:12s} | {a["account_type"]:12s} | '
          f'{a["display_name"]:30s} | {a["status"]:8s} | {a["id"]}')

print("\n=== Facebook Page (cloudless.gr) ===")
print(api_py(f"""
import asyncio
from app.core.security import decrypt_token
from app.db.session import engine
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

async def main():
    async with AsyncSession(engine) as db:
        result = await db.execute(text("SELECT access_token_enc, account_id FROM social_accounts WHERE id='{FB_PAGE_ACCOUNT_ID}'"))
        row = result.fetchone()
        token = decrypt_token(row[0])
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f'https://graph.facebook.com/v19.0/{{row[1]}}', params={{'access_token': token, 'fields': 'name,about,description,website,phone'}})
            d = r.json()
            print(f'  Name: {{d.get("name")}}')
            print(f'  About: {{d.get("about","")[:80]}}')
            print(f'  Website: {{d.get("website")}}')
            print(f'  Phone: {{d.get("phone")}}')
            desc = d.get('description','')
            print(f'  Description has cloudless.gr: {{"cloudless.gr" in desc}}')
            print(f'  Description has portfolio: {{"baltzakisthemis" in desc}}')
            print(f'  Description has WhatsApp: {{"wa.me" in desc}}')

asyncio.run(main())
"""))

print("\n=== LinkedIn Organization (cloudless.gr) ===")
c = get(f"{LI_SIDECAR}/company/cloudless-gr").get("company", {})
if c:
    print(f'  Name: {c.get("name")}')
    print(f'  About: {c.get("about","")[:80]}')
    print(f'  Website: {c.get("website")}')
    print(f'  Specialties: {c.get("specialties")}')
else:
    print("  (sidecar not available)")

print("\n=== LinkedIn Personal ===")
p = get(f"{LI_SIDECAR}/profile").get("profile", {})
if p:
    about = p.get("about", "")
    print(f'  Headline: {p.get("headline")}')
    print(f"  About: {about[:80]}")
    print(f'  Website: {p.get("website")}')
    print(f'  About has cloudless.gr: {"cloudless.gr" in about}')
    print(f'  About has portfolio: {"baltzakisthemis" in about}')
else:
    print("  (sidecar not available)")

print("\n=== Instagram ===")
d = get(f"{IG_API}/account")
if d:
    print(f'  Username: {d.get("username")}')
    print(f'  Biography: {d.get("biography","")[:80]}')
    print(f'  External URL: {d.get("external_url")}')
else:
    print("  (sidecar not logged in)")
