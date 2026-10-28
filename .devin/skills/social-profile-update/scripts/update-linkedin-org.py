#!/usr/bin/env python3
"""Update LinkedIn Company Page description and/or about via the
Organizations API.
Usage: update-linkedin-org.py "New description" "New about/specialties"
Requires: LinkedIn org account connected with rw_organization_admin scope."""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import repo_root, request, social_api, usage  # noqa: E402

description = sys.argv[1] if len(sys.argv) > 1 else ""
about = sys.argv[2] if len(sys.argv) > 2 else ""
if not description and not about:
    usage('update-linkedin-org.py "description text" "about/specialties text"')

api, token = social_api()
accounts = request("GET", f"{api}/api/v1/accounts", token=token)
org_account = next(
    (a["id"] for a in (accounts if isinstance(accounts, list) else accounts.get("accounts", []))
     if a["platform"] == "linkedin" and a.get("account_type") == "organization"), "")
if not org_account:
    print("No LinkedIn organization account found.", file=sys.stderr)
    sys.exit(1)

print(f"LinkedIn org account: {org_account}")
print("Updating profile via social-api...")

r = subprocess.run(
    ["docker", "compose", "exec", "-T", "social-api", "python3", "-c", """
import asyncio, json, sys
from sqlalchemy import select
from app.db.session import async_session_maker
from app.models.social_account import SocialAccount
from app.core.security import decrypt_token
import httpx

DESCRIPTION = %s
ABOUT = %s
ACCOUNT_ID = "%s"

async def main():
    async with async_session_maker() as db:
        result = await db.execute(
            select(SocialAccount).where(SocialAccount.id == ACCOUNT_ID)
        )
        acct = result.scalar_one()
        token = decrypt_token(acct.access_token_enc)
        org_id = acct.account_id

        patch = {}
        if DESCRIPTION:
            patch['description'] = {'value': DESCRIPTION}
        if ABOUT:
            patch['specialties'] = {'values': [{'value': ABOUT}]}

        print(f'Patching org {org_id} with: {json.dumps(list(patch.keys()))}')

        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                f'https://api.linkedin.com/rest/organizations/{org_id}',
                headers={
                    'Authorization': f'Bearer {token}',
                    'LinkedIn-Version': '202501',
                },
                params={'projection': '(id,localizedName,localizedDescription,localizedSpecialties)'}
            )
            if r.status_code != 200:
                print(f'ERROR reading profile: {r.status_code} {r.text[:300]}')
                return
            current = r.json()
            print(f'Current name: {current.get("localizedName","")}')
            print(f'Current description: {current.get("localizedDescription","")[:100]}...')

            r2 = await client.post(
                f'https://api.linkedin.com/rest/organizations/{org_id}',
                headers={
                    'Authorization': f'Bearer {token}',
                    'Content-Type': 'application/json',
                    'X-Restli-Method': 'PARTIAL_UPDATE',
                    'LinkedIn-Version': '202501',
                },
                json=patch
            )
            if r2.status_code in (200, 204):
                print('SUCCESS: LinkedIn org profile updated')
            else:
                print(f'ERROR updating: {r2.status_code} {r2.text[:300]}')

asyncio.run(main())
""" % (repr(description), repr(about), org_account)],
    cwd=repo_root(), capture_output=True, text=True)
out = "\n".join(l for l in (r.stdout + r.stderr).splitlines()
                if not any(s in l for s in ("sqlalchemy", "INFO", "BEGIN", "ROLLBACK", "SELECT", "WHERE", "FROM")))
print(out)
