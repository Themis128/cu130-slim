#!/usr/bin/env python3
# ruff: noqa: E402
"""Bluesky (AT Protocol) ops tooling — reads bluesky SocialAccounts +
encrypted app passwords from the SocialAuto DB, then hits the account's PDS.

Run inside the social-api container:

    docker exec social-api python3 /app/scripts/bluesky_tool.py <cmd>

Commands:
    accounts    list bluesky SocialAccounts (id, handle, DID, PDS, status)
    status      live session check + profile (followers/follows/posts)
    facets <t>  dry-run the richtext facet detector on a text string —
                see byte offsets and detected links/mentions/hashtags
                without posting

Options: -a/--account ACCOUNT_UUID (defaults to first bluesky account).
Never prints app passwords or JWTs.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

for path in ("/app", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
    if path not in sys.path and os.path.isdir(os.path.join(path, "app")):
        sys.path.insert(0, path)


async def _accounts() -> list:
    from sqlalchemy import select

    from app.db.session import async_session_maker
    from app.models.social_account import SocialAccount

    async with async_session_maker() as db:
        result = await db.execute(
            select(SocialAccount).where(SocialAccount.platform == "bluesky")
        )
        return list(result.scalars().all())


def _client_for(account):
    from app.core.security import decrypt_token
    from app.services.bluesky_api import BSKY_PDS_DEFAULT, BlueskyClient

    enc = account.access_token_enc
    app_password = decrypt_token(enc if isinstance(enc, bytes) else enc.encode())
    pds = (account.meta_data or {}).get("pds_url") or BSKY_PDS_DEFAULT
    return BlueskyClient(account.username or "", app_password, pds_url=pds)


async def cmd_accounts() -> None:
    for a in await _accounts():
        pds = (a.meta_data or {}).get("pds_url") or "https://bsky.social"
        print(
            f"{a.id}  @{a.username or '-'}  did={a.account_id}  "
            f"pds={pds}  status={a.status}"
        )


async def cmd_status(account) -> None:
    from app.services.bluesky_api import BlueskyAPIError

    client = _client_for(account)
    try:
        await client.create_session()
        p = await client.get_profile()
    except BlueskyAPIError as exc:
        print(f"@{account.username}  session FAILED: {exc.status_code} {exc.response_text[:200]}")
        return
    print(
        f"@{p.get('handle')}  did={p.get('did')}\n"
        f"  followers={p.get('followersCount')}  follows={p.get('followsCount')}  "
        f"posts={p.get('postsCount')}\n"
        f"  display_name={p.get('displayName') or '-'}  created={p.get('createdAt', '-')[:10]}"
    )


def cmd_facets(text: str) -> None:
    from app.services.bluesky_api import build_facets

    facets = build_facets(text)
    if not facets:
        print("no facets detected")
        return
    for f in facets:
        idx = f["index"]
        span = text.encode("utf-8")[idx["byteStart"]:idx["byteEnd"]].decode("utf-8")
        feat = f["features"][0]
        kind = feat["$type"].rsplit("#", 1)[-1]
        detail = feat.get("uri") or feat.get("tag") or feat.get("handle") or feat.get("did") or ""
        print(f"  [{idx['byteStart']}..{idx['byteEnd']}] {kind}: {span!r} → {detail}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cmd", choices=("accounts", "status", "facets"))
    parser.add_argument("arg", nargs="?", default=None)
    parser.add_argument("-a", "--account", default=None)
    args = parser.parse_args()

    if args.cmd == "accounts":
        await cmd_accounts()
        return 0
    if args.cmd == "facets":
        if not args.arg:
            print("usage: facets <text>", file=sys.stderr)
            return 2
        cmd_facets(args.arg)
        return 0

    accounts = await _accounts()
    if not accounts:
        print("no bluesky accounts found", file=sys.stderr)
        return 1
    account = next(
        (a for a in accounts if str(a.id) == args.account),
        accounts[0] if args.account is None else None,
    )
    if account is None:
        print(f"account {args.account} not found", file=sys.stderr)
        return 1

    if args.cmd == "status":
        await cmd_status(account)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
