#!/usr/bin/env python3
"""Probe the FB Page <-> Instagram linkage state end-to-end.

Prints, for the cloudless.gr Page (116436681562585):
  - Graph instagram_business_account / connected_instagram_account
  - the social_accounts instagram rows (status + account_id)
  - the exact remediation URLs when linkage is missing

Usage:  python3 ig_link_probe.py          (runs inside social-api)
No secrets are printed — only booleans and IDs.
"""
import json
import subprocess
import sys

PAGE_ID = "116436681562585"
BUSINESS_ID = "1134463867"  # business.facebook.com portfolio hosting the page

CODE = r'''
import asyncio, json, urllib.request, urllib.error
from app.db.session import async_session_maker
from sqlalchemy import text
from app.core.security import decrypt_token

def get(u):
    try:
        return json.loads(urllib.request.urlopen(u, timeout=15).read())
    except urllib.error.HTTPError as he:
        return {"_http": he.code, "_body": he.read()[:200].decode(errors="replace")}
    except Exception as e:
        return {"_err": str(e)[:120]}

async def main():
    out = {}
    async with async_session_maker() as s:
        r = await s.execute(text(
            "select access_token_enc from social_accounts "
            "where platform='facebook' and status='active' "
            "and username not ilike '%.gr' limit 1"))
        enc = r.scalar()
        r2 = await s.execute(text(
            "select username, status, account_type, account_id "
            "from social_accounts where platform='instagram'"))
        out["ig_rows"] = [dict(x._mapping) for x in r2]
    if not enc:
        out["error"] = "no active facebook user token"
        print(json.dumps(out)); return
    tok = decrypt_token(enc)
    d = get("https://graph.facebook.com/v21.0/me/accounts"
            "?access_token=" + tok +
            "&fields=id,name,instagram_business_account,"
            "connected_instagram_account,access_token")
    for p in d.get("data", []):
        if p.get("id") == "''' + PAGE_ID + '''":
            p.pop("access_token", None)
            out["page"] = p
    print(json.dumps(out))

asyncio.run(main())
'''


def main():
    r = subprocess.run(
        ["docker", "exec", "-i", "social-api", "python", "-"],
        input=CODE, capture_output=True, text=True, timeout=90)
    line = next((l for l in r.stdout.splitlines()
                 if l.strip().startswith("{")), "")
    if not line:
        print("probe failed:", (r.stderr or r.stdout)[-300:])
        return 1
    d = json.loads(line)
    page = d.get("page") or {}
    igbiz = bool(page.get("instagram_business_account"))
    igconn = bool(page.get("connected_instagram_account"))
    print(f"page {PAGE_ID}: instagram_business_account={igbiz} "
          f"connected_instagram_account={igconn}")
    for row in d.get("ig_rows", []):
        print("  ig row:", row.get("username"), row.get("status"),
              row.get("account_type"), "id=", row.get("account_id"))
    if igbiz and igconn:
        print("LINKED — full business connection is live.")
        return 0
    print("NOT LINKED. Remediation:")
    print("  1. Page settings link surface:")
    print("     https://www.facebook.com/settings/?tab=linked_instagram"
          "  (switch into the Page first)")
    print("  2. If it reports 'resolve issues with your Facebook account':")
    print(f"     https://business.facebook.com/business-support-home/{BUSINESS_ID}/"
          "  (accounts list — FB-account vs ad-account vs portfolio flags)")
    print("  3. Graph API cannot create the link — read-only fields.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
