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
    rotate      rotate the stored app password via the account MAIN
                password (BSKY_MAIN_PASSWORD env) — createSession →
                createAppPassword → verify → persist. Use after 401s
                ("Invalid identifier or password").
    auth-status read-only auth health: stored password, persisted session
                expiry, shared createSession circuit state, 24h 401 count,
                BSKY_MAIN_PASSWORD readiness. Makes NO login call itself.

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
        result = await db.execute(select(SocialAccount).where(SocialAccount.platform == "bluesky"))
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
        print(f"{a.id}  @{a.username or '-'}  did={a.account_id}  pds={pds}  status={a.status}")


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
        span = text.encode("utf-8")[idx["byteStart"] : idx["byteEnd"]].decode("utf-8")
        feat = f["features"][0]
        kind = feat["$type"].rsplit("#", 1)[-1]
        detail = feat.get("uri") or feat.get("tag") or feat.get("handle") or feat.get("did") or ""
        print(f"  [{idx['byteStart']}..{idx['byteEnd']}] {kind}: {span!r} → {detail}")


async def cmd_auth_status(account) -> None:
    """Read-only auth health report — no createSession call, so it never
    burns the rate-limited login budget itself."""
    import time
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import func, select

    from app.db.session import async_session_maker
    from app.models.content import PostTarget

    meta = account.meta_data or {}
    sess = meta.get("bsky_session") or {}
    access_jwt = sess.get("access_jwt") or ""
    exp = None
    if access_jwt:
        try:
            from app.services.bluesky_api import BlueskyClient

            exp = BlueskyClient._jwt_exp(access_jwt)
        except Exception:  # noqa: BLE001
            exp = None
    pds = meta.get("pds_url") or "https://bsky.social"

    # Shared createSession circuit state (Redis) — opened by any 429.
    circuit = "unknown"
    try:
        import redis.asyncio as aioredis

        from app.core.config import get_settings
        from app.services.bluesky_api import _BSKY_RL_KEY

        r = aioredis.from_url(get_settings().REDIS_URL, decode_responses=True)
        try:
            until = await r.get(_BSKY_RL_KEY)
        finally:
            await r.aclose()
        if until:
            left = float(until) - time.time()
            circuit = f"OPEN ({left / 60:.0f}min left)" if left > 0 else "closed"
        else:
            circuit = "closed"
    except Exception:  # noqa: BLE001
        pass

    since = datetime.now(UTC) - timedelta(hours=24)
    async with async_session_maker() as db:
        fails = (
            await db.execute(
                select(func.count())
                .select_from(PostTarget)
                .where(PostTarget.social_account_id == account.id)
                .where(PostTarget.status == "failed")
                .where(PostTarget.updated_at >= since)
                .where(PostTarget.error_message.ilike("%AuthenticationRequired%"))
            )
        ).scalar() or 0

    main_pw = bool(os.environ.get("BSKY_MAIN_PASSWORD"))
    print(f"@{account.username or '-'}  did={account.account_id}  pds={pds}")
    print(f"  stored app password: {'yes' if account.access_token_enc else 'NO'}")
    print(f"  persisted bsky_session: {'yes' if sess else 'no'}"
          + (f" (access_jwt exp {'valid' if exp and exp > time.time() else 'expired/stale'}, saved {str(sess.get('saved_at'))[:19]})" if sess else ""))
    print(f"  createSession circuit: {circuit}")
    print(f"  failed targets w/ 401 (24h): {fails}")
    print(f"  BSKY_MAIN_PASSWORD configured: {'yes' if main_pw else 'NO — rotation impossible until set'}")
    if fails:
        print("  → run: bluesky_tool.py rotate   (needs BSKY_MAIN_PASSWORD)")


async def cmd_rotate(account) -> int:
    from app.db.session import async_session_maker
    from app.services.bluesky_api import rotate_app_password

    main_password = os.environ.get("BSKY_MAIN_PASSWORD") or ""
    if not main_password:
        print("set BSKY_MAIN_PASSWORD to the account's MAIN Bluesky password", file=sys.stderr)
        return 2
    async with async_session_maker() as db:
        acc = await db.get(type(account), account.id)
        if acc is None:
            print("account not found", file=sys.stderr)
            return 1
        await rotate_app_password(acc, main_password, db)
    print("rotated app password for", account.username or account.id)
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cmd", choices=("accounts", "status", "facets", "rotate", "auth-status"))
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
    elif args.cmd == "auth-status":
        await cmd_auth_status(account)
    elif args.cmd == "rotate":
        return await cmd_rotate(account)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
