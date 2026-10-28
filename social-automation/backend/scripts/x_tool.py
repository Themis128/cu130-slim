#!/usr/bin/env python3
# ruff: noqa: E402
"""X (Twitter) ops tooling — reads twitter SocialAccounts + encrypted tokens
from the SocialAuto DB, then hits the official X API v2.

Complements app/api/twitter.py (publish/OAuth surface) and the browser
fallback path (browser-ops skill); this is the read-mostly account/API ops
surface for terminal use.

Run inside the social-api container:

    docker exec social-api python3 /app/scripts/x_tool.py <cmd>

Commands:
    accounts         list twitter SocialAccounts (id, handle, status)
    validate         /2/users/me token check (id, username)
    me               /2/users/me with public_metrics — followers, following,
                     tweet count, verified flag (growth tracking for the
                     FB-Stars funnel account)
    user <handle>    /2/users/by/username/:h — public metrics for any X
                     account (funnel/competitor lookups)
    tweet <id>       /2/tweets/:id — text, created_at, public_metrics
    recent [n]       own recent tweets with metrics (default 5, max 100)
    quota            parse x-app-limit / x-user-limit headers off users/me —
                     X quota state without burning a write call

Options: -a/--account ACCOUNT_UUID (defaults to first twitter account).
Never prints tokens.
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
            select(SocialAccount).where(SocialAccount.platform == "twitter")
        )
        return list(result.scalars().all())


def _client_for(account):
    from app.core.security import decrypt_token
    from app.services.twitter_api import TwitterAPIClient

    enc = account.access_token_enc
    token = decrypt_token(enc if isinstance(enc, bytes) else enc.encode())
    return TwitterAPIClient(access_token=token)


def _fmt_metrics(m: dict | None) -> str:
    m = m or {}
    return "  ".join(
        f"{k}={m.get(k, 0)}" for k in (
            "followers_count", "following_count", "tweet_count",
            "listed_count", "like_count",
        )
        if k in m
    )


async def cmd_accounts() -> None:
    for a in await _accounts():
        print(
            f"{a.id}  @{a.username or '-'}  user_id={a.account_id}  "
            f"status={a.status}  scopes={','.join(a.scopes or []) or '-'}"
        )


async def cmd_validate(account) -> None:
    data = (await _client_for(account).validate_token()).get("data") or {}
    print(
        f"@{data.get('username', '-')}  id={data.get('id', '-')}  "
        f"name={data.get('name', '-')}"
    )


async def cmd_me(account) -> None:
    data = (await _client_for(account).get_me_metrics()).get("data") or {}
    print(
        f"@{data.get('username', '-')}  id={data.get('id', '-')}  "
        f"verified={data.get('verified')}"
    )
    print(f"  {_fmt_metrics(data.get('public_metrics'))}")
    if data.get("created_at"):
        print(f"  created_at={data['created_at']}")


async def cmd_user(account, handle: str) -> None:
    data = (await _client_for(account).get_user_by_username(handle)).get("data") or {}
    print(
        f"@{data.get('username', '-')}  id={data.get('id', '-')}  "
        f"verified={data.get('verified')}  name={data.get('name', '-')}"
    )
    print(f"  {_fmt_metrics(data.get('public_metrics'))}")
    if data.get("description"):
        print(f"  bio: {data['description'][:120]}")


async def cmd_tweet(account, tweet_id: str) -> None:
    data = (await _client_for(account).get_tweet(tweet_id)).get("data") or {}
    m = data.get("public_metrics") or {}
    print(
        f"tweet {data.get('id')}  created_at={data.get('created_at', '-')}\n"
        f"  impressions={m.get('impression_count', '-')} likes={m.get('like_count', '-')} "
        f"retweets={m.get('retweet_count', '-')} replies={m.get('reply_count', '-')} "
        f"quotes={m.get('quote_count', '-')} bookmarks={m.get('bookmark_count', '-')}\n"
        f"  text: {(data.get('text') or '')[:200]}"
    )


async def cmd_recent(account, n: int) -> None:
    client = _client_for(account)
    me = (await client.validate_token()).get("data") or {}
    tweets = await client.get_user_tweets(me["id"], max_results=min(max(n, 5), 100))
    for t in (tweets.get("data") or [])[:n]:
        m = t.get("public_metrics") or {}
        print(
            f"{t.get('id')}  {t.get('created_at', '-')[:10]}  "
            f"imp={m.get('impression_count', '-')} likes={m.get('like_count', '-')} "
            f"rt={m.get('retweet_count', '-')}  {(t.get('text') or '')[:80]!r}"
        )


async def cmd_quota(account) -> None:
    """users/me response headers expose rate-limit state."""
    import httpx

    client = _client_for(account)
    url = f"{client.api_base}/users/me"
    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.get(url, headers=client._headers())
    for k, v in sorted(resp.headers.items()):
        if "limit" in k.lower() or "reset" in k.lower():
            print(f"  {k}: {v}")
    print(f"  http_status: {resp.status_code}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cmd", choices=(
        "accounts", "validate", "me", "user", "tweet", "recent", "quota",
    ))
    parser.add_argument("arg", nargs="?", default=None)
    parser.add_argument("-a", "--account", default=None)
    args = parser.parse_args()

    if args.cmd == "accounts":
        await cmd_accounts()
        return 0

    accounts = await _accounts()
    if not accounts:
        print("no twitter accounts found", file=sys.stderr)
        return 1
    account = next(
        (a for a in accounts if str(a.id) == args.account),
        accounts[0] if args.account is None else None,
    )
    if account is None:
        print(f"account {args.account} not found", file=sys.stderr)
        return 1

    try:
        if args.cmd == "validate":
            await cmd_validate(account)
        elif args.cmd == "me":
            await cmd_me(account)
        elif args.cmd == "user":
            if not args.arg:
                print("usage: user <handle>", file=sys.stderr)
                return 2
            await cmd_user(account, args.arg)
        elif args.cmd == "tweet":
            if not args.arg:
                print("usage: tweet <tweet_id>", file=sys.stderr)
                return 2
            await cmd_tweet(account, args.arg)
        elif args.cmd == "recent":
            await cmd_recent(account, int(args.arg) if args.arg else 5)
        elif args.cmd == "quota":
            await cmd_quota(account)
    except Exception as exc:
        from app.services.twitter_api import TwitterAPIError

        if isinstance(exc, TwitterAPIError):
            print(f"X API error {exc.status_code}: {exc.response_text[:300]}", file=sys.stderr)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
