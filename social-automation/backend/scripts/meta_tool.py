#!/usr/bin/env python3
"""Meta Graph tooling via the official facebook_business SDK.

Runs inside the social-api container (has DB access + the SDK):

    docker exec social-api python3 /app/scripts/meta_tool.py <cmd>

Commands:
    accounts                          list Meta accounts (platform, username, token expiry)
    debug-token --account <uuid>      Graph debug_token: validity, scopes, expiry
    pages --account <uuid>            pages the user/page token can manage
    insights --account <uuid> [--days N]  page insights sample (impressions/fans)

Never prints tokens. Output is JSON-friendly text.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid as uuid_mod

# Allow running as /app/scripts/meta_tool.py inside the container, or from the
# repo on a host with the backend path injected (same pattern as linkedin_cli).
_CONTAINER = "/app"
_REPO_BACKEND = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
for path in (_CONTAINER, _REPO_BACKEND):
    if path not in sys.path and os.path.isdir(os.path.join(path, "app")):
        sys.path.insert(0, path)


async def _get_account(account_id: str):
    from sqlalchemy import select

    from app.db.session import async_session_maker
    from app.models.social_account import SocialAccount

    async with async_session_maker() as db:
        row = (
            await db.execute(
                select(SocialAccount).where(SocialAccount.id == uuid_mod.UUID(account_id))
            )
        ).scalar_one_or_none()
        if row is None:
            raise SystemExit(f"no such social account: {account_id}")
        # detach for use after session close
        db.expunge(row)
        return row


def _token(account) -> str:
    from app.core.security import decrypt_token

    return decrypt_token(bytes(account.access_token_enc))


def _app_token() -> str:
    from app.core.config import settings

    cid = settings.FACEBOOK_CLIENT_ID
    secret = settings.FACEBOOK_APP_SECRET or settings.FACEBOOK_CLIENT_SECRET
    if not (cid and secret):
        raise SystemExit("FACEBOOK_CLIENT_ID / app secret not configured")
    return f"{cid}|{secret}"


def _init(token: str) -> None:
    from facebook_business.api import FacebookAdsApi

    FacebookAdsApi.init(access_token=token, crash_log=False)


def cmd_accounts() -> None:
    async def _run():
        from sqlalchemy import select

        from app.db.session import async_session_maker
        from app.models.social_account import SocialAccount

        async with async_session_maker() as db:
            rows = (
                await db.execute(
                    select(SocialAccount).where(
                        SocialAccount.platform.in_(
                            ["facebook", "instagram", "threads", "messenger"]
                        )
                    )
                )
            ).scalars().all()
            for r in rows:
                print(
                    f"{r.id}\t{r.platform}\t{r.username or r.account_id}\t"
                    f"expires={r.token_expires_at}\tstatus={r.status}"
                )

    asyncio.run(_run())


def cmd_debug_token(account_id: str) -> None:
    account = asyncio.run(_get_account(account_id))
    import requests

    resp = requests.get(
        "https://graph.facebook.com/v21.0/debug_token",
        params={
            "input_token": _token(account),
            "access_token": _app_token(),
        },
        timeout=15,
    )
    data = resp.json().get("data", {})
    print(
        json.dumps(
            {
                "platform": account.platform,
                "username": account.username,
                "is_valid": data.get("is_valid"),
                "expires_at": data.get("expires_at"),
                "data_access_expires_at": data.get("data_access_expires_at"),
                "scopes": data.get("scopes"),
                "granular_scopes": [
                    s.get("scope") for s in data.get("granular_scopes", [])
                ],
                "error": data.get("error"),
            },
            indent=2,
        )
    )


def cmd_pages(account_id: str) -> None:
    account = asyncio.run(_get_account(account_id))
    _init(_token(account))
    from facebook_business.adobjects.user import User

    for page in User(fbid="me").get_accounts(fields=["name", "id", "access_token"]):
        print(f"{page['id']}\t{page.get('name')}")


def cmd_insights(account_id: str, days: int) -> None:
    account = asyncio.run(_get_account(account_id))
    _init(_token(account))
    from facebook_business.adobjects.page import Page
    from facebook_business.exceptions import FacebookRequestError

    page = Page(fbid=account.account_id)
    # Requested individually — Meta deprecates page_* metrics between API
    # versions, and one bad metric fails the whole call.
    candidates = [
        "page_impressions",
        "page_post_engagements",
        "page_views_total",
        "page_daily_follows",
        "page_fan_adds",
        "page_total_actions",
    ]
    for metric in candidates:
        try:
            insights = page.get_insights(
                params={
                    "metric": [metric],
                    "period": "day",
                    "since": f"-{days} days",
                }
            )
            for m in insights:
                vals = [v.get("value") for v in m.get("values", [])[-3:]]
                print(f"{m.get('name')}: {vals}")
        except FacebookRequestError as exc:
            print(f"{metric}: unavailable ({exc.api_error_code()})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("accounts")
    p = sub.add_parser("debug-token")
    p.add_argument("--account", required=True)
    p = sub.add_parser("pages")
    p.add_argument("--account", required=True)
    p = sub.add_parser("insights")
    p.add_argument("--account", required=True)
    p.add_argument("--days", type=int, default=7)
    args = ap.parse_args()

    if args.cmd == "accounts":
        cmd_accounts()
    elif args.cmd == "debug-token":
        cmd_debug_token(args.account)
    elif args.cmd == "pages":
        cmd_pages(args.account)
    elif args.cmd == "insights":
        cmd_insights(args.account, args.days)


if __name__ == "__main__":
    main()
