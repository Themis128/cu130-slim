#!/usr/bin/env python3
"""li_member_snapshot.py — pull 1st-degree connections via LinkedIn's
Member Data Portability API (EEA members only — replaces the manual
Connections.csv export). Emails/names feed the survey audience path.

Requires on the app: "Member Data Portability API (Member)" product +
scope r_dma_portability_member (or _3rd_party). Version must be 202312.

    li_member_snapshot.py <account_id> [--attach <CID>]
"""
import asyncio
import sys
import uuid

import httpx
from sqlalchemy import select

API = "https://api.linkedin.com/rest/memberSnapshotData"
VER = "202312"


async def _account(account_id: str):
    from app.db.session import async_session_maker
    from app.models.social_account import SocialAccount

    async with async_session_maker() as db:
        row = (await db.execute(
            select(SocialAccount).where(SocialAccount.id == uuid.UUID(account_id)))
        ).scalar_one_or_none()
        if row is None:
            raise SystemExit(f"no such account {account_id}")
        db.expunge(row)
        return row


async def _snapshot(account_id: str) -> list[dict]:
    from app.core.security import decrypt_token

    acc = await _account(account_id)
    tok = decrypt_token(bytes(acc.access_token_enc))
    out, start = [], 0
    async with httpx.AsyncClient(timeout=30) as c:
        while True:
            r = await c.get(
                API,
                params={"q": "criteria", "domain": "CONNECTIONS",
                        "start": start, "count": 50},
                headers={"Authorization": f"Bearer {tok}",
                         "Linkedin-Version": VER,
                         "Content-Type": "application/json"})
            if r.status_code != 200:
                print(r.status_code, r.text[:300])
                return out
            d = r.json()
            out.extend(d.get("elements") or [])
            paging = d.get("paging") or {}
            if not paging.get("links"):
                break
            start += paging.get("count", 50)
            if len(out) >= paging.get("total", 0):
                break
    return out


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    rows = asyncio.run(_snapshot(sys.argv[1]))
    print(f"{len(rows)} connections")
    for r in rows[:30]:
        name = (r.get("firstName") or "") + " " + (r.get("lastName") or "")
        email = r.get("emailAddress") or ""
        print(f"{name.strip()}\t{email}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
