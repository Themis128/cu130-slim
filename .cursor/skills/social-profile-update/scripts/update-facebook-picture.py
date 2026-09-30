#!/usr/bin/env python3
"""Update Facebook Page profile picture from a media library asset.
Usage: update-facebook-picture.py <media_asset_id>
Requires: Facebook Page account connected with MANAGE task permission.
The media asset must have a publicly accessible URL (/api/v1/media/view)."""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import env, repo_root, request, social_api, usage  # noqa: E402

media_id = sys.argv[1] if len(sys.argv) > 1 else usage("update-facebook-picture.py <media_asset_id>")

api, token = social_api()
accounts = request("GET", f"{api}/api/v1/accounts", token=token)
page_account = next(
    (a["id"] for a in (accounts if isinstance(accounts, list) else accounts.get("accounts", []))
     if a["platform"] == "facebook" and a.get("is_business")), "")
if not page_account:
    print("No Facebook Page (business) account found.", file=sys.stderr)
    sys.exit(1)

base = env("MEDIA_PUBLIC_BASE_URL") or "https://social.cloudless.gr"
image_url = f"{base}/api/v1/media/view?path={media_id}"

print(f"Facebook Page account: {page_account}")
print(f"Image URL: {image_url}")

r = subprocess.run(
    ["docker", "compose", "exec", "-T", "social-api", "python3", "-c", """
import asyncio, sys
from sqlalchemy import select
from app.db.session import async_session_maker
from app.models.social_account import SocialAccount
from app.core.security import decrypt_token
import httpx

IMAGE_URL = %s
ACCOUNT_ID = "%s"

async def main():
    async with async_session_maker() as db:
        result = await db.execute(
            select(SocialAccount).where(SocialAccount.id == ACCOUNT_ID)
        )
        acct = result.scalar_one()
        token = decrypt_token(acct.access_token_enc)
        page_id = acct.account_id

        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f'https://graph.facebook.com/v21.0/{page_id}/picture',
                params={'url': IMAGE_URL, 'access_token': token}
            )
            resp = r.json()
            if resp.get('success'):
                print('SUCCESS: Facebook Page profile picture updated')
            else:
                print(f'ERROR: {r.status_code} {r.text[:300]}')

asyncio.run(main())
""" % (repr(image_url), page_account)], cwd=repo_root(), capture_output=True, text=True)
out = "\n".join(l for l in (r.stdout + r.stderr).splitlines()
                if not any(s in l for s in ("sqlalchemy", "INFO", "BEGIN", "ROLLBACK", "SELECT", "WHERE", "FROM")))
print(out)
