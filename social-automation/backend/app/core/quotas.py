"""Plan-based quota limits for the SaaS tiering system.

Each plan tier defines monthly limits for posts, AI calls, and social
accounts. A value of ``-1`` means *unlimited*.
"""

from __future__ import annotations

PLAN_LIMITS: dict[str, dict[str, int]] = {
    "free": {
        "posts_per_month": 10,
        "ai_calls_per_month": 50,
        "social_accounts": 1,
    },
    "pro": {
        "posts_per_month": 100,
        "ai_calls_per_month": 500,
        "social_accounts": 5,
    },
    "business": {
        "posts_per_month": -1,  # unlimited
        "ai_calls_per_month": 5000,
        "social_accounts": 20,
    },
    "enterprise": {
        "posts_per_month": -1,
        "ai_calls_per_month": -1,
        "social_accounts": -1,
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
