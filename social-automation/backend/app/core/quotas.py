"""Plan-based quota limits for the SaaS tiering system.

Each plan tier defines monthly limits for posts, AI calls, and social
accounts. A value of ``-1`` means *unlimited*.
"""

from __future__ import annotations

# Limits must match the Polar/Dodo product descriptions and the public
# pricing page — MoR verification checks that advertised and enforced
# quotas agree.
PLAN_LIMITS: dict[str, dict[str, int]] = {
    "free": {
        "posts_per_month": 50,
        "ai_calls_per_month": 100,
        "social_accounts": 3,
        "dm_auto_reply": 0,  # DM automation is a paid feature
    },
    "pro": {
        "posts_per_month": 500,
        "ai_calls_per_month": 5000,
        "social_accounts": 15,
        "dm_auto_reply": 1,
    },
    "business": {
        "posts_per_month": 2000,
        "ai_calls_per_month": 20000,
        "social_accounts": 50,
        "dm_auto_reply": 1,
    },
    "enterprise": {
        "posts_per_month": -1,
        "ai_calls_per_month": -1,
        "social_accounts": -1,
        "dm_auto_reply": 1,
    },
}


def get_plan_limits(tier: str) -> dict[str, int]:
    """Return the full limits dict for *tier*, defaulting to ``free``."""
    return PLAN_LIMITS.get(tier, PLAN_LIMITS["free"])


def get_effective_limit(tier: str, resource: str) -> int:
    """Return the numeric limit for *resource* under *tier*.

    ``-1`` means unlimited. Unknown tiers default to ``free``.
    """
    limits = get_plan_limits(tier)
    return limits.get(resource, -1)


def plan_has_feature(tier: str, feature: str) -> bool:
    """Return True if *feature* is enabled under *tier*.

    Features are stored in ``PLAN_LIMITS`` with flag semantics:
    ``0`` means the feature is unavailable, any non-zero value means
    available. Features absent from a tier default to available so
    existing features are never accidentally paywalled.
    """
    limits = get_plan_limits(tier)
    return limits.get(feature, 1) != 0
