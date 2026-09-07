"""Usage tracking API — current month usage vs plan limits and 12-month history."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, TeamId
from app.core.quotas import get_plan_limits
from app.models.ai_usage import AIUsageLog
from app.models.content import Post, PostStatus
from app.models.social_account import SocialAccount
from app.models.user import Team

router = APIRouter()


def _month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


@router.get("")
async def get_usage(
    team_id: TeamId,
    current_user: CurrentUser,
    db: DbSession,
) -> dict:
    """Return the current team's plan tier and month-to-date usage vs limits."""
    # Resolve the team's plan tier
    result = await db.execute(select(Team.plan_tier).where(Team.id == team_id))
    tier = result.scalar_one_or_none() or "free"

    limits = get_plan_limits(tier)
    now = datetime.now(UTC)
    month_start = _month_start(now)

    # AI calls this month
    ai_result = await db.execute(
        select(func.count(AIUsageLog.id)).where(
            AIUsageLog.team_id == team_id,
            AIUsageLog.created_at >= month_start,
        )
    )
    ai_used = ai_result.scalar_one()

    # Posts this month (excluding archived)
    posts_result = await db.execute(
        select(func.count(Post.id)).where(
            Post.team_id == team_id,
            Post.created_at >= month_start,
            Post.status != PostStatus.ARCHIVED,
        )
    )
    posts_used = posts_result.scalar_one()

    # Social accounts (total, not monthly)
    accounts_result = await db.execute(
        select(func.count(SocialAccount.id)).where(SocialAccount.team_id == team_id)
    )
    accounts_used = accounts_result.scalar_one()

    return {
        "plan_tier": tier,
        "usage": {
            "posts_per_month": {"used": posts_used, "limit": limits["posts_per_month"]},
            "ai_calls_per_month": {"used": ai_used, "limit": limits["ai_calls_per_month"]},
            "social_accounts": {"used": accounts_used, "limit": limits["social_accounts"]},
        },
    }


@router.get("/history")
async def get_usage_history(
    team_id: TeamId,
    current_user: CurrentUser,
    db: DbSession,
) -> list[dict]:
    """Return 12-month usage history (posts and AI calls per month)."""
    now = datetime.now(UTC)

    # Build the list of the last 12 months (oldest first)
    months: list[tuple[int, int]] = []
    for i in range(11, -1, -1):
        year = now.year - (now.month - 1 - i < 0)  # not needed; compute below
        month_idx = now.month - 1 - i
        if month_idx < 0:
            month_idx += 12
            year = now.year - 1
        else:
            year = now.year
        months.append((year, month_idx + 1))

    # Query posts grouped by month
    post_rows = await db.execute(
        select(
            func.extract("year", Post.created_at).label("y"),
            func.extract("month", Post.created_at).label("m"),
            func.count(Post.id).label("cnt"),
        ).where(
            Post.team_id == team_id,
            Post.status != PostStatus.ARCHIVED,
            Post.created_at >= datetime(months[0][0], months[0][1], 1, tzinfo=UTC),
        ).group_by("y", "m")
    )
    post_map: dict[tuple[int, int], int] = {}
    for row in post_rows:
        post_map[(int(row.y), int(row.m))] = int(row.cnt)

    # Query AI usage grouped by month
    ai_rows = await db.execute(
        select(
            func.extract("year", AIUsageLog.created_at).label("y"),
            func.extract("month", AIUsageLog.created_at).label("m"),
            func.count(AIUsageLog.id).label("cnt"),
        ).where(
            AIUsageLog.team_id == team_id,
            AIUsageLog.created_at >= datetime(months[0][0], months[0][1], 1, tzinfo=UTC),
        ).group_by("y", "m")
    )
    ai_map: dict[tuple[int, int], int] = {}
    for row in ai_rows:
        ai_map[(int(row.y), int(row.m))] = int(row.cnt)

    return [
        {
            "month": f"{y:04d}-{m:02d}",
            "posts": post_map.get((y, m), 0),
            "ai_calls": ai_map.get((y, m), 0),
        }
        for y, m in months
    ]
