"""Monetization-readiness tracker — per-platform creator-program thresholds
evaluated against live metrics.

Each entry in ``PLATFORM_PROGRAMS`` is a criterion SocialAuto can evaluate
from metrics it already collects (follower snapshots, platform stat calls)
or flag as ``manual`` when only the platform UI can answer it. The point:
monetization is the primary goal, so progress toward each program's gate
should be measurable in one report instead of checked by hand per console.

Threshold sources (verify before relying — programs change):
- FB Stars (professional mode): 500 followers held 30 consecutive days,
  eligible country, age ≥18, community-standards compliance.
- TikTok Creator Rewards: ≥10,000 followers + ≥100k video views in 30 days.
- X Ads Revenue Sharing: Premium/Premium+ + ≥500 followers + ≥5M organic
  impressions in trailing 3 months.
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
    met: bool | None          # None = cannot be determined via API (manual)
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
            ("days_held", "held ≥500 for 30 consecutive days", 30,
             "computed from follower_snapshots; needs daily sync"),
            ("country_eligible", "eligible country", True,
             "manual — Greece is eligible; confirm in professional dashboard"),
            ("account_standing", "community standards clean", True,
             "manual — check professional dashboard for strikes"),
        ],
    },
    "tiktok": {
        "program": "TikTok Creator Rewards",
        "criteria": [
            ("followers", "followers ≥ 10,000", 10_000, "user.info stats followerCount"),
            ("views_30d", "≥100,000 video views in last 30 days", 100_000,
             "sum video play counts from Display API (video.list)"),
            ("country_eligible", "eligible country", True,
             "manual — program is region-gated"),
        ],
    },
    "twitter": {
        "program": "X Ads Revenue Sharing",
        "criteria": [
            ("followers", "followers ≥ 500", 500, "users/me public_metrics"),
            ("premium", "X Premium / Premium+ active", True,
             "manual — subscription state isn't API-readable"),
            ("impressions_90d", "≥5M organic impressions / 90 days", 5_000_000,
             "pay-per-use endpoint — check in X analytics UI when needed"),
        ],
    },
    "instagram": {
        "program": "IG Subscriptions / Gifts",
        "criteria": [
            ("followers", "followers ≥ 10,000 (eligibility band)", 10_000,
             "insights follower_count; programs are invite-gated"),
            ("invite", "monetization invite received", True,
             "manual — Professional dashboard → monetization"),
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
    "program": "Page monetization (Stars / in-stream ads — page-tier)",
    "criteria": [
        ("followers", "page followers ≥ 5,000 (Stars invite band)", 5_000,
         "page fan count; programs are invite-gated"),
        ("invite", "monetization invite received", True,
         "manual — professional dashboard → monetization"),
    ],
}


def evaluate_program(
    platform: str, metrics: dict[str, Any], account_type: str | None = None
) -> ProgramReport | None:
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
