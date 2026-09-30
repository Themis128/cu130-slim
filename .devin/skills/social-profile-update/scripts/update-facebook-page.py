#!/usr/bin/env python3
"""Update Facebook Page about/description/website via the Graph API.
Usage:
  update-facebook-page.py --about "New about" --description "New desc" --website "https://cloudless.gr"
Requires: Facebook Page account connected with MANAGE task permission."""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import repo_root, request, social_api, usage  # noqa: E402

fields = {}
i = 1
while i < len(sys.argv):
    key = sys.argv[i].lstrip("-")
    if key not in ("about", "description", "website", "phone") or i + 1 >= len(sys.argv):
        usage('update-facebook-page.py --about "text" --description "text" '
              '--website "url" --phone "number"')
    fields[key] = sys.argv[i + 1]
    i += 2

if not fields:
    usage('update-facebook-page.py --about "text" --description "text" '
          '--website "url" --phone "number"')

api, token = social_api()
accounts = request("GET", f"{api}/api/v1/accounts", token=token)
page_account = next(
    (a["id"] for a in (accounts if isinstance(accounts, list) else accounts.get("accounts", []))
     if a["platform"] == "facebook" and a.get("is_business")), "")

if not page_account:
    print("No Facebook Page (business) account found.", file=sys.stderr)
    sys.exit(1)

print(f"Facebook Page account: {page_account}")

r = subprocess.run(
    ["docker", "compose", "exec", "-T", "social-api", "python3", "-c", """
import asyncio, sys
from sqlalchemy import select
from app.db.session import async_session_maker
from app.models.social_account import SocialAccount
from app.core.security import decrypt_token
import httpx

FIELDS = %s
ACCOUNT_ID = "%s"

async def main():
    async with async_session_maker() as db:
        result = await db.execute(
            select(SocialAccount).where(SocialAccount.id == ACCOUNT_ID)
        )
        acct = result.scalar_one()
        token = decrypt_token(acct.access_token_enc)
        page_id = acct.account_id

        payload = {'access_token': token, **FIELDS}
        print(f'Updating Facebook Page {page_id} with: {list(FIELDS)}')

        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                f'https://graph.facebook.com/v21.0/{page_id}',
                params={'fields': 'name,about,description,website,phone', 'access_token': token}
            )
            current = r.json()
            print(f'Current name: {current.get("name","")}')
            print(f'Current about: {current.get("about","")[:80]}')

            r2 = await client.post(
                f'https://graph.facebook.com/v21.0/{page_id}',
                data=payload
            )
            resp = r2.json()
            if resp.get('success'):
                print('SUCCESS: Facebook Page profile updated')
            else:
                print(f'ERROR updating: {r2.status_code} {r2.text[:300]}')

asyncio.run(main())
""" % (repr(fields), page_account)], cwd=repo_root(), capture_output=True, text=True)
out = "\n".join(l for l in (r.stdout + r.stderr).splitlines()
                if not any(s in l for s in ("sqlalchemy", "INFO", "BEGIN", "ROLLBACK", "SELECT", "WHERE", "FROM")))
print(out)
