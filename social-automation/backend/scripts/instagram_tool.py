#!/usr/bin/env python3
# ruff: noqa: E402
"""Instagram Graph API ops tooling — reads IG SocialAccounts + encrypted
tokens from the SocialAuto DB, then hits the official IG Graph API.

Complements app/api/instagram.py + the content/publish paths; this is a
read-mostly ops surface for terminal use (quota checks, insights probing,
hashtag/business discovery).

Run inside the social-api container:

    docker exec social-api python3 /app/scripts/instagram_tool.py <cmd>

Commands:
    accounts        list Instagram SocialAccounts (id, handle, type, status)
    me              profile basics (username, followers, media count)
    quota           content-publishing quota used/remaining (25/24h)
    insights        probe a metric list over a period — flags deprecated ones
    demographics    follower_demographics breakdown (age|country|city|gender)
    engaged         engaged_audience_demographics breakdown
    media           recent media with like/comment counts
    hashtag <q>     ig_hashtag_search + top_media for a hashtag
    discover <user> business_discovery — public metrics of another account

Options: -a/--account ACCOUNT_UUID (defaults to first instagram account).
Never prints tokens.
"""
from __future__ import annotations

import argparse
import asyncio
import json
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
            select(SocialAccount).where(SocialAccount.platform == "instagram")
        )
        return list(result.scalars().all())


def _client_for(account):
    from app.core.security import decrypt_token
    from app.services.instagram_api import InstagramAPIClient

    enc = account.access_token_enc
    token = decrypt_token(enc if isinstance(enc, bytes) else enc.encode())
    return InstagramAPIClient(
        access_token=token,
        ig_user_id=account.account_id,
        use_business_login_api=(account.meta_data or {}).get("login_type")
        == "business_login",
    )


async def _pick(account_id: str | None):
    accounts = await _accounts()
    if not accounts:
        raise SystemExit("no instagram accounts connected")
    if account_id:
        for a in accounts:
            if str(a.id).startswith(account_id):
                return a
        raise SystemExit(f"no instagram account matching {account_id}")
    return accounts[0]


async def cmd_accounts() -> None:
    for a in await _accounts():
        meta = a.meta_data or {}
        print(
            f"{a.id}  @{a.username or '-'}  {a.display_name or ''}  "
            f"type={meta.get('login_type', '-')}  status={a.status}"
        )


async def cmd_me(account_id: str | None) -> None:
    account = await _pick(account_id)
    print(json.dumps(await _client_for(account).get_profile(), indent=2))


async def cmd_quota(account_id: str | None) -> None:
    account = await _pick(account_id)
    client = _client_for(account)
    limit = await client.get_publishing_limit()
    remaining = await client.get_remaining_publish_quota()
    print(json.dumps({"remaining": remaining, "raw": limit}, indent=2))


async def cmd_insights(account_id: str | None, days: int, metric: str | None) -> None:
    account = await _pick(account_id)
    client = _client_for(account)
    metrics = [metric] if metric else [
        "reach", "follower_count", "profile_views", "accounts_engaged",
        "total_interactions", "website_clicks",
        "impressions",  # deprecated on newer versions — expected to flag
    ]
    out = {}
    for m in metrics:
        try:
            data = await client.get_account_insights(metric=m, period="day")
            out[m] = data.get("data")
        except Exception as exc:
            out[m] = f"ERROR: {exc}"
    print(json.dumps(out, indent=2))


async def cmd_demographics(account_id: str | None, breakdown: str) -> None:
    account = await _pick(account_id)
    print(
        json.dumps(
            await _client_for(account).get_follower_demographics(breakdown=breakdown),
            indent=2,
        )
    )


async def cmd_engaged(account_id: str | None, breakdown: str) -> None:
    account = await _pick(account_id)
    print(
        json.dumps(
            await _client_for(account).get_engaged_audience_demographics(
                breakdown=breakdown
            ),
            indent=2,
        )
    )


async def cmd_media(account_id: str | None, limit: int) -> None:
    account = await _pick(account_id)
    rows = await _client_for(account).list_recent_media(limit=limit)
    for m in rows:
        cap = (m.get("caption") or "").replace("\n", " ")[:60]
        print(f"{m.get('timestamp','')[:10]}  {m.get('id')}  {cap}  {m.get('permalink')}")


def _graph_only_note(exc: Exception) -> None:
    """ig_hashtag_search / business_discovery live on graph.facebook.com —
    business_login accounts (graph.instagram.com) get IGApiException 100."""
    print(
        json.dumps(
            {
                "error": "capability_unavailable",
                "detail": str(exc)[:300],
                "note": (
                    "This endpoint requires a Facebook-Login IG token "
                    "(graph.facebook.com). The account uses business_login "
                    "(graph.instagram.com) — reconnect via the FB OAuth flow "
                    "to enable it."
                ),
            },
            indent=2,
        )
    )


async def cmd_hashtag(account_id: str | None, query: str) -> None:
    account = await _pick(account_id)
    client = _client_for(account)
    try:
        found = await client.search_hashtag(query)
    except Exception as exc:
        _graph_only_note(exc)
        return
    data = found.get("data") or []
    if not data:
        print(f"(no hashtag found for {query!r})")
        return
    for h in data[:5]:
        print(f"hashtag id={h['id']} name={h.get('name')}")
    hid = data[0]["id"]
    top = await client.get_hashtag_top_media(hid, limit=10)
    for m in top:
        cap = (m.get("caption") or "").replace("\n", " ")[:60]
        print(
            f"  top  likes={m.get('like_count', '-')} comments={m.get('comments_count', '-')}  {cap}"
        )


async def cmd_discover(account_id: str | None, username: str) -> None:
    account = await _pick(account_id)
    try:
        print(
            json.dumps(
                await _client_for(account).business_discovery(username),
                indent=2,
            )
        )
    except Exception as exc:
        _graph_only_note(exc)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("-a", "--account", default=None, help="account UUID (prefix ok)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("accounts")
    sub.add_parser("me")
    sub.add_parser("quota")
    p_ins = sub.add_parser("insights")
    p_ins.add_argument("--days", type=int, default=7)
    p_ins.add_argument("--metric", default=None)
    p_demo = sub.add_parser("demographics")
    p_demo.add_argument("--breakdown", default="age", choices=["age", "country", "city", "gender"])
    p_eng = sub.add_parser("engaged")
    p_eng.add_argument("--breakdown", default="age", choices=["age", "country", "city", "gender"])
    p_media = sub.add_parser("media")
    p_media.add_argument("--limit", type=int, default=15)
    p_tag = sub.add_parser("hashtag")
    p_tag.add_argument("query")
    p_dis = sub.add_parser("discover")
    p_dis.add_argument("username")
    args = ap.parse_args()

    coro = {
        "accounts": lambda: cmd_accounts(),
        "me": lambda: cmd_me(args.account),
        "quota": lambda: cmd_quota(args.account),
        "insights": lambda: cmd_insights(args.account, args.days, args.metric),
        "demographics": lambda: cmd_demographics(args.account, args.breakdown),
        "engaged": lambda: cmd_engaged(args.account, args.breakdown),
        "media": lambda: cmd_media(args.account, args.limit),
        "hashtag": lambda: cmd_hashtag(args.account, args.query),
        "discover": lambda: cmd_discover(args.account, args.username),
    }[args.cmd]()
    asyncio.run(coro)


if __name__ == "__main__":
    main()
