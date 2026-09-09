"""Centralized FastAPI dependencies for team-scoped access control.

These dependencies eliminate the repeated ``select(Team).join(TeamMember)...``
pattern scattered across routers and ensure every resource lookup is
team-scoped by default.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user, oauth2_scheme
from app.core.security import decode_token
from app.db.session import get_db
from app.models.user import Team, TeamMember, User, UserRole

CurrentUser = Annotated[User, Depends(get_current_user)]
DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_team_id(
    current_user: CurrentUser,
    db: DbSession,
    token: str = Depends(oauth2_scheme),
) -> uuid.UUID:
    """Return the user's active team ID.

    The team is resolved in priority order:
      1. ``team_id`` claim in the JWT (set by ``POST /auth/switch-team``).
      2. The first team membership for the user (fallback for legacy tokens).
    Raises 403 if the user has no team.
    """
    # 1. Check for a team_id embedded in the access token.
    payload = decode_token(token)
    jwt_team_id = payload.get("team_id") if payload else None
    if jwt_team_id:
        candidate = uuid.UUID(jwt_team_id)
        # Validate that the user is actually a member of that team.
        result = await db.execute(
            select(TeamMember).where(
                TeamMember.team_id == candidate,
                TeamMember.user_id == current_user.id,
            )
        )
        if result.scalar_one_or_none() is not None:
            return candidate

    # 2. Fallback: first team membership.
    result = await db.execute(
        select(Team.id).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team_id = result.scalars().first()
    if team_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not a member of any team",
        )
    return team_id  # type: ignore[return-value]


TeamId = Annotated[uuid.UUID, Depends(get_current_team_id)]


async def get_current_team(
    current_user: CurrentUser,
    db: DbSession,
) -> Team:
    """Return the user's Team object, raising 403 if they have no team."""
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()
    if team is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not a member of any team",
        )
    return team


CurrentTeam = Annotated[Team, Depends(get_current_team)]


# ── Team-scoped role enforcement ────────────────────────────────────────────

_ROLE_LEVEL = {
    UserRole.VIEWER: 0,
    UserRole.EDITOR: 1,
    UserRole.ADMIN: 2,
    UserRole.OWNER: 3,
}


async def get_user_role_in_team(
    user: User,
    team_id: uuid.UUID,
    db: AsyncSession,
) -> UserRole:
    """Return the user's role in a specific team (defaults to VIEWER)."""
    result = await db.execute(
        select(TeamMember.role).where(
            TeamMember.user_id == user.id,
            TeamMember.team_id == team_id,
        )
    )
    role = result.scalar_one_or_none()
    return role or UserRole.VIEWER


def require_team_role(min_role: UserRole):
    """FastAPI dependency factory: require at least ``min_role`` in the team.

    Unlike the legacy ``require_role`` in auth.py, this resolves the team
    context and checks the role within that specific team.
    """

    async def _check(
        current_user: CurrentUser,
        team_id: TeamId,
        db: DbSession,
    ) -> User:
        role = await get_user_role_in_team(current_user, team_id, db)
        if _ROLE_LEVEL.get(role, 0) < _ROLE_LEVEL[min_role]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires {min_role.value} role or higher in this team",
            )
        return current_user

    return _check


require_team_admin = require_team_role(UserRole.ADMIN)
require_team_owner = require_team_role(UserRole.OWNER)
require_team_editor = require_team_role(UserRole.EDITOR)


# ── Quota / plan-limit enforcement ──────────────────────────────────────────


async def check_quota(resource: str, team_id: uuid.UUID, db: AsyncSession) -> None:
    """Check if the team has exceeded their plan limit for *resource*.

    Raises ``HTTPException(429)`` if the team is over its plan limit.
    A limit of ``-1`` means unlimited and always passes.

    The platform admin (identified by ``SOCIAL_ADMIN_EMAIL``) is always
    exempt from quota enforcement.
    """
    from datetime import UTC, datetime

    from sqlalchemy import func

    from app.core.config import get_settings
    from app.core.quotas import get_effective_limit
    from app.models.ai_usage import AIUsageLog
    from app.models.content import Post, PostStatus
    from app.models.social_account import SocialAccount

    # Admin bypass: the platform admin is never limited
    settings = get_settings()
    admin_email = getattr(settings, "SOCIAL_ADMIN_EMAIL", None)
    if admin_email:
        result = await db.execute(
            select(Team.owner_id).where(Team.id == team_id)
        )
        owner_id = result.scalar_one_or_none()
        if owner_id:
            result = await db.execute(
                select(User.email).where(User.id == owner_id)
            )
            owner_email = result.scalar_one_or_none()
            if owner_email and owner_email == admin_email:
                return  # Admin is exempt from all quotas

    # Get team's plan tier
    result = await db.execute(select(Team.plan_tier).where(Team.id == team_id))
    tier = result.scalar_one_or_none() or "free"

    limit = get_effective_limit(tier, resource)
    if limit == -1:
        return  # unlimited

    # Calculate current month usage
    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    if resource == "ai_calls_per_month":
        result = await db.execute(
            select(func.count(AIUsageLog.id)).where(
                AIUsageLog.team_id == team_id,
                AIUsageLog.created_at >= month_start,
            )
        )
        usage = result.scalar_one()
    elif resource == "posts_per_month":
        result = await db.execute(
            select(func.count(Post.id)).where(
                Post.team_id == team_id,
                Post.created_at >= month_start,
                Post.status != PostStatus.ARCHIVED,
            )
        )
        usage = result.scalar_one()
    elif resource == "social_accounts":
        result = await db.execute(
            select(func.count(SocialAccount.id)).where(SocialAccount.team_id == team_id)
        )
        usage = result.scalar_one()
    else:
        return

    if int(usage) >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Plan limit exceeded: {resource} ({usage}/{limit}). Upgrade your plan to continue.",
            headers={
                "X-Quota-Resource": resource,
                "X-Quota-Used": str(usage),
                "X-Quota-Limit": str(limit),
            },
        )

    # Fire a quota warning email at 80% usage (non-fatal, fire-and-forget).
    # Only warn once per resource per month — check email_logs for an existing
    # warning this month to avoid spamming on every call.
    try:
        usage_int = int(usage)
        if limit > 0 and usage_int >= int(limit * 0.8):
            from app.models.email_log import EmailLog

            existing = await db.execute(
                select(func.count(EmailLog.id)).where(
                    EmailLog.team_id == team_id,
                    EmailLog.template == "quota_warning",
                    EmailLog.subject.contains(resource),
                    EmailLog.created_at >= month_start,
                )
            )
            if existing.scalar_one() == 0:
                # Find the team owner to email.
                owner_result = await db.execute(
                    select(User).join(Team, Team.owner_id == User.id).where(Team.id == team_id)
                )
                owner = owner_result.scalar_one_or_none()
                if owner is not None:
                    # Respect notification preferences — default to True if unset.
                    prefs = owner.notification_preferences or {}
                    if not prefs.get("email_on_quota", True):
                        return
                    import asyncio

                    from app.services.email_templates import send_quota_warning_email

                    asyncio.create_task(
                        send_quota_warning_email(owner, resource, usage_int, limit)
                    )
    except Exception:
        pass  # non-fatal — quota check must never fail on email issues
