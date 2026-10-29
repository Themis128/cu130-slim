#!/usr/bin/env python3
# ruff: noqa: E402
"""LinkedIn Community Management ops — org page posts/comments/reactions
via the versioned REST API (app 264925843, Development Tier).

    python scripts/linkedin_cm_tool.py posts <account_id>
    python scripts/linkedin_cm_tool.py comments <account_id> <post_urn>
    python scripts/linkedin_cm_tool.py scopes <account_id>
"""

import asyncio
import sys
import uuid as uuid_mod

import httpx
from sqlalchemy import select

API = "https://api.linkedin.com/rest"
VER = "202510"
ORG_ACCOUNT = "9c4451bb-e820-489f-8676-76ddbc788ffe"


async def _account(account_id: str):
    from app.db.session import async_session_maker
    from app.models.social_account import SocialAccount

    async with async_session_maker() as db:
        row = (await db.execute(select(SocialAccount).where(SocialAccount.id == uuid_mod.UUID(account_id)))).scalar_one_or_none()
        if row is None:
            raise SystemExit(f"no such account {account_id}")
        db.expunge(row)
        return row


def _token(acc) -> str:
    from app.core.security import decrypt_token

    return decrypt_token(bytes(acc.access_token_enc))


def _urn(acc) -> str:
    meta = acc.meta_data or {}
    urn = meta.get("author_urn") or ""
    if urn.startswith("urn:li:organization:"):
        return urn
    return f"urn:li:organization:{acc.account_id}"


def _headers(tok: str) -> dict:
    return {
        "Authorization": f"Bearer {tok}",
        "Linkedin-Version": VER,
        "X-Restli-Protocol-Version": "2.0.0",
    }


def cmd_posts(account_id: str) -> None:
    async def _run():
        acc = await _account(account_id)
        urn = _urn(acc)
        import urllib.parse

        q = urllib.parse.quote(urn, safe="")
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(
                f"{API}/posts?q=author&author={q}&count=10",
                headers=_headers(_token(acc)),
            )
            if r.status_code in (403, 426):  # Dev Tier: no /rest — legacy v2
                a = "List(" + urllib.parse.quote(urn, safe="") + ")"
                r = await c.get(
                    f"https://api.linkedin.com/v2/ugcPosts?q=authors&authors={a}&count=10",
                    headers=_headers(_token(acc)),
                )
        print(r.status_code)
        d = r.json() if r.status_code == 200 else {"err": r.text[:300]}
        for p in d.get("elements") or []:
            body = (
                p.get("commentary") or (p.get("specificContent", {}).get("com.linkedin.ugc.ShareContent", {}).get("shareCommentary", {}).get("text")) or ""
            )[:80].replace("\n", " ")
            print(f"{p.get('id')}\t{body}")
        if "err" in d:
            print(d)

    asyncio.run(_run())


def cmd_comments(account_id: str, post_urn: str) -> None:
    async def _run():
        acc = await _account(account_id)
        import urllib.parse

        q = urllib.parse.quote(post_urn, safe="")
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(
                f"{API}/socialActions/{q}/comments",
                headers=_headers(_token(acc)),
            )
            if r.status_code in (403, 426):  # Dev Tier: no /rest — legacy v2
                r = await c.get(
                    f"https://api.linkedin.com/v2/socialActions/{q}/comments",
                    headers=_headers(_token(acc)),
                )
        print(r.status_code, r.text[:500])

    asyncio.run(_run())


def cmd_scopes(account_id: str) -> None:
    async def _run():
        acc = await _account(account_id)
        tok = _token(acc)
        async with httpx.AsyncClient(timeout=20) as c:
            for probe in ["userinfo", "organizations/264925843", "posts?q=author&author=x"]:
                r = await c.get(f"{API if 'posts' in probe or 'organizations' in probe else 'https://api.linkedin.com/v2'}/{probe}", headers=_headers(tok))
                print(probe, r.status_code)

    asyncio.run(_run())


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "posts" and len(sys.argv) > 2:
        cmd_posts(sys.argv[2])
    elif cmd == "comments" and len(sys.argv) > 3:
        cmd_comments(sys.argv[2], sys.argv[3])
    elif cmd == "scopes" and len(sys.argv) > 2:
        cmd_scopes(sys.argv[2])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
