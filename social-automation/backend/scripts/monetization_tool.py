#!/usr/bin/env python3
# ruff: noqa: E402
"""Monetization-readiness report — evaluates every connected social account
against its platform's creator-program thresholds (FB Stars, TikTok Creator
Rewards, X Ads Revenue Share, IG Subscriptions/Gifts).

Metrics come from live platform API calls where possible and from
follower_snapshots for counts APIs can't reach (personal FB profile) and
for the 30-day-hold / trend calculations.

Run inside the social-api container:

    docker exec social-api python3 /app/scripts/monetization_tool.py <cmd>

Commands:
    report      full per-platform monetization-readiness table
    growth      follower-snapshot trend per account (last 30d, net/day)

Never prints tokens.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta

for path in ("/app", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
    if path not in sys.path and os.path.isdir(os.path.join(path, "app")):
        sys.path.insert(0, path)


async def _accounts() -> list:
    from sqlalchemy import select

    from app.db.session import async_session_maker
    from app.models.social_account import SocialAccount

    async with async_session_maker() as db:
        result = await db.execute(
            select(SocialAccount).where(SocialAccount.status == "active")
        )
        return list(result.scalars().all())


def _token(account) -> str:
    from app.core.security import decrypt_token

    enc = account.access_token_enc
    return decrypt_token(enc if isinstance(enc, bytes) else enc.encode())


async def _snapshots(account, days: int = 60) -> list[tuple[datetime, int]]:
    from sqlalchemy import select

    from app.db.session import async_session_maker
    from app.models.analytics import FollowerSnapshot

    cutoff = datetime.now(UTC) - timedelta(days=days)
    async with async_session_maker() as db:
        rows = await db.execute(
            select(FollowerSnapshot.captured_at, FollowerSnapshot.followers)
            .where(
                FollowerSnapshot.social_account_id == account.id,
                FollowerSnapshot.captured_at >= cutoff,
            )
            .order_by(FollowerSnapshot.captured_at)
        )
        return [(r[0], r[1]) for r in rows.all()]


def _days_held(snaps: list[tuple[datetime, int]], threshold: int) -> int:
    """Consecutive days (by UTC date) the follower count has been ≥ threshold."""
    by_day: dict[str, int] = {}
    for ts, n in snaps:
        key = (ts if ts.tzinfo else ts.replace(tzinfo=UTC)).date().isoformat()
        by_day[key] = max(by_day.get(key, 0), n)
    held = 0
    day = datetime.now(UTC).date()
    while by_day.get(day.isoformat(), 0) >= threshold:
        held += 1
        day -= timedelta(days=1)
    return held


def _daily_gain(snaps: list[tuple[datetime, int]], days: int = 14) -> float:
    recent = [(t, n) for t, n in snaps if t >= datetime.now(UTC) - timedelta(days=days)]
    if len(recent) < 2:
        return 0.0
    span = max((recent[-1][0] - recent[0][0]).days, 1)
    return (recent[-1][1] - recent[0][1]) / span


async def _followers_live(account) -> int | None:
    """Best-effort live follower count via the platform client."""
    try:
        if account.platform == "instagram":
            from app.services.instagram_api import InstagramAPIClient

            meta = account.meta_data or {}
            client = InstagramAPIClient(
                _token(account),
                account.account_id,
                use_business_login_api=meta.get("login_type") == "business_login",
            )
            return int((await client.get_profile()).get("followers_count") or 0) or None
        if account.platform == "tiktok":
            from app.services.tiktok_api import TikTokAPIClient

            client = TikTokAPIClient(access_token=_token(account), open_id=account.account_id)
            data = (await client.get_stats()).get("data") or {}
            return int(data.get("follower_count") or 0) or None
        if account.platform == "twitter":
            from app.services.twitter_api import TwitterAPIClient

            data = (await TwitterAPIClient(_token(account)).get_me_metrics()).get("data") or {}
            return int((data.get("public_metrics") or {}).get("followers_count") or 0)
    except Exception as exc:
        print(f"  [{account.platform}] live metrics failed: {str(exc)[:120]}")
    return None


async def _tiktok_views_30d(account) -> int | None:
    try:
        from app.services.tiktok_api import TikTokAPIClient

        client = TikTokAPIClient(access_token=_token(account), open_id=account.account_id)
        cutoff = datetime.now(UTC).timestamp() - 30 * 86400
        total = 0
        cursor = 0
        while True:
            data = (await client.list_videos(cursor=cursor, max_count=20)).get("data") or {}
            vids = data.get("videos") or []
            if not vids:
                break
            for v in vids:
                if (v.get("create_time") or 0) >= cutoff:
                    total += int(v.get("view_count") or 0)
            if not data.get("has_more"):
                break
            cursor = data.get("cursor") or 0
        return total
    except Exception as exc:
        print(f"  [tiktok] views_30d failed: {str(exc)[:120]}")
        return None


async def cmd_report() -> None:
    from app.services.monetization import FUNNEL_ONLY, days_to_goal, evaluate_program

    for a in await _accounts():
        platform = a.platform
        handle = f"@{a.username}" if a.username else a.account_id[:12]
        print(f"\n{platform.upper()}  {handle}  ({str(a.id)[:8]})")

        snaps = await _snapshots(a)
        snap_followers = snaps[-1][1] if snaps else None
        live_followers = await _followers_live(a)
        followers = live_followers if live_followers is not None else snap_followers
        gain = _daily_gain(snaps)

        metrics: dict = {"followers": followers}
        if platform == "tiktok":
            metrics["views_30d"] = await _tiktok_views_30d(a)
        if platform == "facebook" and isinstance(followers, int):
            metrics["days_held"] = _days_held(snaps, 500)

        report = evaluate_program(platform, metrics, account_type=a.account_type)
        if report is None:
            print(f"  {FUNNEL_ONLY.get(platform, 'no monetization program')}")
            if followers is not None:
                print(f"  followers: {followers}  ({'live' if live_followers else 'snapshot'}, +{gain:.1f}/day)")
            continue

        print(f"  program: {report.program}")
        for c in report.criteria:
            mark = "✓" if c.met is True else ("✗" if c.met is False else "?")
            cur = c.current if c.current is not None else "—"
            print(f"  {mark} {c.name}: {cur} (needs {c.target})  {c.note}")
        eta = days_to_goal(followers or 0, 500, gain) if platform == "facebook" else None
        if followers is not None:
            eta_txt = f"  ~{eta}d to goal at current pace" if eta else "  (not growing at current pace)"
            print(f"  followers: {followers}  ({'live' if live_followers else 'snapshot'}, {gain:+.1f}/day){eta_txt}")


async def cmd_growth() -> None:
    for a in await _accounts():
        snaps = await _snapshots(a, days=30)
        handle = f"@{a.username}" if a.username else a.account_id[:12]
        if not snaps:
            print(f"{a.platform:10s} {handle:24s} no snapshots")
            continue
        first, last = snaps[0], snaps[-1]
        gain = _daily_gain(snaps)
        print(
            f"{a.platform:10s} {handle:24s} {first[1]:>6} → {last[1]:>6} "
            f"({gain:+.2f}/day over {len(snaps)} snapshots)"
        )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cmd", choices=("report", "growth"))
    args = parser.parse_args()
    if args.cmd == "report":
        await cmd_report()
    elif args.cmd == "growth":
        await cmd_growth()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
