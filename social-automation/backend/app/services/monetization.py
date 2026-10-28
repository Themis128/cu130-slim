"""Monetization-readiness tracker — per-platform creator-program thresholds
evaluated against live metrics.

Each entry in ``PLATFORM_PROGRAMS`` is a criterion SocialAuto can evaluate
from metrics it already collects (follower snapshots, platform stat calls)
or flag as ``manual`` when only the platform UI can answer it. The point:
monetization is the primary goal, so progress toward each program's gate
should be measurable in one report instead of checked by hand per console.

Threshold sources (verify before relying — programs change):
- FB Stars (professional mode): 500 followers held 30 consecutive days,
  eligible country, age ≥18, community-standards compliance. Still a
  standalone program after the 2025 consolidation.
- FB Content Monetization (pages + professional profiles): INVITE-ONLY —
  replaced In-stream ads, Ads on Reels and the Performance bonus, which all
  ended 2025-08-31 (Meta Business Help Center). Meta publishes no numeric
  threshold; the interest form in Professional dashboard → Monetization is
  a waitlist, not an application. Observed dashboard variants ask for
  ~10k followers / 50k engagements / 180k views.
- FB Subscriptions (pages): ≥10,000 followers OR ≥250 returning viewers
  plus engagement bar; invite-gated.
- Creator Fast Track (announced 2026-03): ≥100k followers on an eligible
  external platform (IG/TikTok/YouTube) → $1k–$3k/month for 3 months +
  instant Content Monetization access. US/CA/UK/AU only.
- TikTok Creator Rewards: ≥10,000 followers + ≥100k video views in 30 days,
  BUT country-gated to US/UK/DE/JP/KR/FR/BR — Greece is NOT eligible
  (verified 2026-10, TikTok Creator Academy). JP/KR terms require ≥50,000
  followers. Personal accounts only — Business accounts are ineligible.
  Rewardable content: >1min original videos with ≥1,000 For-You-feed views;
  Duets/Stitches/Photo Mode/sponsored content don't earn.
- X Original Content Rewards (replaced Creator Revenue Sharing — new
  enrollments closed 2026-08-07, program retired 2026-09-07): active
  Premium/Premium+/Premium Business + ≥500 VERIFIED followers + ≥500k
  Home Timeline impressions from verified users in trailing 90 days,
  paid on qualified impressions from Premium users.
- Instagram Subscriptions/Gifts: invite-gated; ~10k followers is the
  de-facto eligibility band (Gifts need 5k+ in eligible regions).
- LinkedIn/Threads/Bluesky/messaging platforms: no creator monetization —
  funnel surfaces (Polar/audit CTA), tracked separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Criterion:
    name: str
    current: Any
    target: Any
    met: bool | None  # None = cannot be determined via API (manual)
    note: str = ""


@dataclass
class ProgramReport:
    platform: str
    program: str
    criteria: list[Criterion]
    monetizable: bool | None  # None = has manual/unknown criteria

    @property
    def met_count(self) -> int:
        return sum(1 for c in self.criteria if c.met is True)


# program name -> list of (key, label, target, note)
PLATFORM_PROGRAMS: dict[str, dict[str, Any]] = {
    "facebook": {
        "program": "Facebook Stars (professional mode)",
        "criteria": [
            ("followers", "followers ≥ 500", 500, "professional-mode profile followers"),
            ("days_held", "held ≥500 for 30 consecutive days", 30, "computed from follower_snapshots; needs daily sync"),
            ("country_eligible", "eligible country", True, "manual — Greece is eligible; confirm in professional dashboard"),
            ("account_standing", "community standards clean", True, "manual — check professional dashboard for strikes"),
            (
                "cm_invite",
                "Content Monetization invite (invite-only program)",
                True,
                "manual — Professional dashboard → monetization; interest form submitted — waits for an invite wave, no numeric threshold",
            ),
        ],
    },
    "tiktok": {
        "program": "TikTok Creator Rewards",
        "criteria": [
            ("followers", "followers ≥ 10,000", 10_000, "user.info stats followerCount"),
            ("views_30d", "≥100,000 video views in last 30 days", 100_000, "sum video play counts from Display API (video.list)"),
            (
                "country_eligible",
                "eligible country",
                True,
                "BLOCKED — program is restricted to US/UK/DE/JP/KR/FR/BR; Greece is not eligible (verified 2026-10); Business accounts are also excluded",
            ),
        ],
    },
    "twitter": {
        "program": "X Original Content Rewards",
        "criteria": [
            ("followers", "verified followers ≥ 500", 500, "users/me public_metrics — verified-only count isn't API-split; total followers is the floor"),
            ("premium", "X Premium / Premium+ / Premium Business active", True, "manual — subscription state isn't API-readable"),
            (
                "impressions_90d",
                "≥500K Home Timeline impressions from verified users / 90 days",
                500_000,
                "not API-readable on free tier — check X analytics UI",
            ),
        ],
    },
    "instagram": {
        "program": "IG Subscriptions / Gifts",
        "criteria": [
            ("followers", "followers ≥ 10,000 (eligibility band)", 10_000, "insights follower_count; programs are invite-gated"),
            ("invite", "monetization invite received", True, "manual — Professional dashboard → monetization"),
        ],
    },
}

# Platforms with no creator monetization — they're funnel surfaces.
FUNNEL_ONLY: dict[str, str] = {
    "linkedin": "No creator monetization — brand funnel to Polar/audit CTA",
    "threads": "Invite-only bonus programs — treated as funnel surface",
    "bluesky": "No monetization program — funnel surface",
    "whatsapp": "Messaging channel — not a creator platform",
    "telegram": "Messaging channel — not a creator platform",
    "viber": "Messaging channel — not a creator platform",
    "messenger": "Messaging channel — not a creator platform",
}


_PLATFORM_PAGE_PROGRAM = {
    "program": "Page monetization (Content Monetization / Subscriptions — invite-only)",
    "criteria": [
        (
            "followers",
            "page followers ≥ 10,000 (Subscriptions threshold band)",
            10_000,
            "page fan count; CM has no published threshold — invite-only since in-stream ads/Ads on Reels/Performance bonus ended 2025-08-31",
        ),
        ("invite", "monetization invite received", True, "manual — professional dashboard → monetization"),
    ],
}


def evaluate_program(platform: str, metrics: dict[str, Any], account_type: str | None = None) -> ProgramReport | None:
    """Evaluate ``metrics`` (followers, views_30d, impressions_90d, days_held,
    …) against ``platform``'s creator program. Returns ``None`` for platforms
    with no program (they're funnel surfaces — see ``FUNNEL_ONLY``).

    ``account_type`` distinguishes FB Pages (different, invite-gated
    programs) from professional-mode personal profiles (Stars)."""
    if platform == "facebook" and account_type and account_type != "user":
        spec = _PLATFORM_PAGE_PROGRAM
    else:
        spec = PLATFORM_PROGRAMS.get(platform)
    if spec is None:
        return None

    criteria: list[Criterion] = []
    for key, label, target, note in spec["criteria"]:
        current = metrics.get(str(key))
        if isinstance(target, bool):
            # boolean criteria are manual confirmations
            met = current if isinstance(current, bool) else None
            criteria.append(Criterion(label, current, "yes", met, note))
        elif isinstance(target, int | float) and isinstance(current, int | float):
            criteria.append(Criterion(label, current, target, current >= target, note))
        else:
            criteria.append(Criterion(label, current, target, None, note))

    if all(c.met is True for c in criteria):
        monetizable: bool | None = True
    elif any(c.met is False for c in criteria):
        monetizable = False
    else:
        monetizable = None  # nothing failing, but manual criteria unknown
    return ProgramReport(platform, spec["program"], criteria, monetizable)


def days_to_goal(current: int, target: int, daily_gain: float) -> int | None:
    """Crude linear estimate of days until ``current`` reaches ``target``
    at ``daily_gain`` net new followers/day. ``None`` when not growing."""
    if current >= target:
        return 0
    if daily_gain <= 0:
        return None
    return round((target - current) / daily_gain)


# ---------------------------------------------------------------------------
# Live metric gathering — service-level version of scripts/monetization_tool.py
# so the daily report can evaluate programs without a subprocess.
# ---------------------------------------------------------------------------


def _account_token(account) -> str:
    from app.core.security import decrypt_token

    enc = account.access_token_enc
    return decrypt_token(enc if isinstance(enc, bytes) else enc.encode())


async def gather_account_metrics(db, account) -> dict[str, Any]:
    """Assemble the metrics dict ``evaluate_program`` consumes for one account.

    followers: live platform call where possible, else latest follower_snapshot.
    days_held: consecutive UTC days at/above 500 followers (FB Stars gate).
    views_30d: TikTok video-view sum over the trailing 30 days.
    Also returns ``gain_per_day`` (14d net/day) for trend display.
    """
    import logging
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from app.models.analytics import FollowerSnapshot

    log = logging.getLogger(__name__)
    cutoff = datetime.now(UTC) - timedelta(days=60)
    rows = await db.execute(
        select(FollowerSnapshot.captured_at, FollowerSnapshot.followers)
        .where(
            FollowerSnapshot.social_account_id == account.id,
            FollowerSnapshot.captured_at >= cutoff,
        )
        .order_by(FollowerSnapshot.captured_at)
    )
    snaps = [(r[0], r[1]) for r in rows.all()]

    live: int | None = None
    try:
        if account.platform == "instagram":
            from app.services.instagram_api import InstagramAPIClient

            meta = account.meta_data or {}
            client = InstagramAPIClient(
                _account_token(account),
                account.account_id,
                use_business_login_api=meta.get("login_type") == "business_login",
            )
            live = int((await client.get_profile()).get("followers_count") or 0) or None
        elif account.platform == "tiktok":
            from app.services.tiktok_api import TikTokAPIClient

            client = TikTokAPIClient(access_token=_account_token(account), open_id=account.account_id)
            data = (await client.get_stats()).get("data") or {}
            live = int(data.get("follower_count") or 0) or None
        elif account.platform == "twitter":
            from app.services.twitter_api import TwitterAPIClient

            data = (await TwitterAPIClient(_account_token(account)).get_me_metrics()).get("data") or {}
            live = int((data.get("public_metrics") or {}).get("followers_count") or 0)
    except Exception as exc:  # noqa: BLE001
        log.info("live followers failed for %s: %s", account.platform, str(exc)[:120])

    followers = live if live is not None else (snaps[-1][1] if snaps else None)

    recent = [s for s in snaps if s[0] >= datetime.now(UTC) - timedelta(days=14) and s[1] > 0]
    # Theil-Sen slope — a single bad scrape or purge at either endpoint
    # corrupts a first→last delta but barely moves the pairwise-slope median.
    # Falls back to the endpoint delta under 3 points.
    from app.services.insights_engine import _theil_sen_slope

    slope = _theil_sen_slope(recent)
    if slope is not None:
        gain = slope
    elif len(recent) >= 2:
        span = max((recent[-1][0] - recent[0][0]).total_seconds() / 86400.0, 1.0)
        gain = (recent[-1][1] - recent[0][1]) / span
    else:
        gain = 0.0

    metrics: dict[str, Any] = {
        "followers": followers,
        "followers_live": live is not None,
        "gain_per_day": round(gain, 2),
        "gain_method": "theil_sen" if slope is not None else ("endpoints" if len(recent) >= 2 else "none"),
    }
    if account.platform == "facebook" and isinstance(followers, int):
        by_day: dict[str, int] = {}
        for ts, n in snaps:
            key = (ts if ts.tzinfo else ts.replace(tzinfo=UTC)).date().isoformat()
            by_day[key] = max(by_day.get(key, 0), n)
        held = 0
        day = datetime.now(UTC).date()
        while by_day.get(day.isoformat(), 0) >= 500:
            held += 1
            day -= timedelta(days=1)
        metrics["days_held"] = held
    if account.platform == "tiktok":
        try:
            from app.services.tiktok_api import TikTokAPIClient

            client = TikTokAPIClient(access_token=_account_token(account), open_id=account.account_id)
            ts_cutoff = datetime.now(UTC).timestamp() - 30 * 86400
            total, cursor = 0, 0
            while True:
                data = (await client.list_videos(cursor=cursor, max_count=20)).get("data") or {}
                vids = data.get("videos") or []
                if not vids:
                    break
                for v in vids:
                    if (v.get("create_time") or 0) >= ts_cutoff:
                        total += int(v.get("view_count") or 0)
                if not data.get("has_more"):
                    break
                cursor = data.get("cursor") or 0
            metrics["views_30d"] = total
        except Exception as exc:  # noqa: BLE001
            log.info("tiktok views_30d failed: %s", str(exc)[:120])
    return metrics
