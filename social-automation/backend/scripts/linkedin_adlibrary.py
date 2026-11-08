#!/usr/bin/env python3
# ruff: noqa: E402
"""LinkedIn Ad Library API ops (GET /rest/adLibrary, Finder q=criteria).

Public ad-transparency search across ANY advertiser — competitor research,
not your own performance data (that's the Marketing API's job).

Product: "LinkedIn Ad Library" Default Tier — provisioned 2026-11-06 on app
265077368 (cloudless-gr-member). Verified live: tokens issued by app
264925843 (77j8kzfi1a6jn8) also pass — the endpoint is member-token gated,
not strictly app-gated.

API quirks (verified 2026-11-06):
- Requires an ACTIVE monthly Linkedin-Version header. 202608/202609/202610
  return 200; older monthly versions (202503, 202507, 202510) return
  426 NONEXISTENT_VERSION. Same rule as the CM API.
- X-Restli-Protocol-Version: 2.0.0 is required.
- Rate limit: ~5s between pages is the documented-safe cadence (see
  community examples); we sleep between paged calls.

Run inside the social-api container:

    docker exec social-api python3 /app/scripts/linkedin_adlibrary.py accounts
    docker exec social-api python3 /app/scripts/linkedin_adlibrary.py \
        search --account <uuid> --keyword "cloud computing"
    docker exec social-api python3 /app/scripts/linkedin_adlibrary.py \
        search --account <uuid> --advertiser "Amazon Web Services"
    docker exec social-api python3 /app/scripts/linkedin_adlibrary.py \
        search --account <uuid> --advertiser amazon --pages 2

Never prints tokens.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid as uuid_mod

for path in ("/app",):
    if path not in sys.path:
        sys.path.insert(0, path)

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402

API_BASE = "https://api.linkedin.com/rest/adLibrary"
API_VERSION = "202610"  # active monthly version — bump when LinkedIn sunsets it
PROTOCOL_VERSION = "2.0.0"
PAGE_DELAY_S = 5.0
MAX_PAGES = 10


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Linkedin-Version": API_VERSION,
        "X-Restli-Protocol-Version": PROTOCOL_VERSION,
        "Content-Type": "application/json",
    }


async def _get_account(account_id: str):
    from app.db.session import async_session_maker
    from app.models.social_account import SocialAccount

    async with async_session_maker() as db:
        row = (
            await db.execute(
                select(SocialAccount).where(
                    SocialAccount.id == uuid_mod.UUID(account_id),
                    SocialAccount.platform == "linkedin",
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise SystemExit(f"no such linkedin social account: {account_id}")
        db.expunge(row)
        return row


def _token(account) -> str:
    from app.core.security import decrypt_token

    return decrypt_token(bytes(account.access_token_enc))


def _summarize(el: dict) -> dict:
    details = el.get("details") or {}
    adv = details.get("advertiser") or {}
    stats = details.get("adStatistics") or {}
    targeting = details.get("adTargeting") or []
    facets = {
        t.get("facetName"): {
            "included": t.get("includedSegments") or [],
            "excluded": t.get("excludedSegments") or [],
        }
        for t in targeting
        if t.get("isIncluded") or t.get("isExcluded")
    }
    impressions = stats.get("totalImpressions") or {}
    return {
        "ad_url": el.get("adUrl"),
        "advertiser": adv.get("advertiserName"),
        "ad_payer": adv.get("adPayer"),
        "advertiser_url": adv.get("advertiserUrl"),
        "is_restricted": el.get("isRestricted"),
        "first_impression_at": stats.get("firstImpressionAt"),
        "latest_impression_at": stats.get("latestImpressionAt"),
        "impressions": impressions,
        "impressions_by_country": stats.get("impressionsDistributionByCountry") or [],
        "targeting": facets,
    }


async def cmd_accounts() -> None:
    from app.db.session import async_session_maker
    from app.models.social_account import SocialAccount

    async with async_session_maker() as db:
        rows = (
            await db.execute(
                select(SocialAccount.id, SocialAccount.username, SocialAccount.account_type, SocialAccount.token_expires_at).where(
                    SocialAccount.platform == "linkedin"
                )
            )
        ).all()
    for r in rows:
        print(f"{r.id}  {r.username}  ({r.account_type})  token_exp={r.token_expires_at}")


async def cmd_search(args) -> None:
    account = await _get_account(args.account)
    token = _token(account)
    pages = min(args.pages, MAX_PAGES)
    collected: list[dict] = []
    total = None
    start = args.start

    async with httpx.AsyncClient(timeout=30) as client:
        for page in range(pages):
            params = {"q": "criteria", "start": start, "count": args.count}
            if args.keyword:
                params["keyword"] = args.keyword
            if args.advertiser:
                params["advertiser"] = args.advertiser
            resp = await client.get(API_BASE, params=params, headers=_headers(token))
            if resp.status_code != 200:
                print(f"HTTP {resp.status_code}: {resp.text[:500]}", file=sys.stderr)
                sys.exit(1)
            data = resp.json()
            total = (data.get("paging") or {}).get("total", total)
            elements = data.get("elements") or []
            if not elements:
                break
            collected.extend(_summarize(e) for e in elements)
            start += len(elements)
            if page + 1 < pages:
                time.sleep(PAGE_DELAY_S)

    print(json.dumps({"total_reported": total, "fetched": len(collected), "ads": collected}, indent=2, default=str))


def main() -> None:
    p = argparse.ArgumentParser(description="LinkedIn Ad Library search")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("accounts", help="list linkedin SocialAccounts usable as token source")

    s = sub.add_parser("search", help="search the public ad library")
    s.add_argument("--account", required=True, help="linkedin SocialAccount UUID")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--keyword", help="keyword search across ad copy")
    g.add_argument("--advertiser", help="advertiser name filter")
    s.add_argument("--start", type=int, default=0)
    s.add_argument("--count", type=int, default=25, help="page size (max ~100)")
    s.add_argument("--pages", type=int, default=1, help=f"pages to fetch (max {MAX_PAGES}, 5s apart)")
    s.set_defaults(fn=cmd_search)

    args = p.parse_args()
    if args.cmd == "accounts":
        asyncio.run(cmd_accounts())
    else:
        asyncio.run(args.fn(args))


if __name__ == "__main__":
    main()
