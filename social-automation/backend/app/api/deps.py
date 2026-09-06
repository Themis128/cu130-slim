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

from app.api.auth import get_current_user
from app.db.session import get_db
from app.models.user import Team, TeamMember, User, UserRole

CurrentUser = Annotated[User, Depends(get_current_user)]
DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_team_id(
    current_user: CurrentUser,
    db: DbSession,
) -> uuid.UUID:
    """Return the user's team ID, raising 403 if they have no team."""
    result = await db.execute(
        select(Team.id).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team_id = result.scalar_one_or_none()
    if team_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not a member of any team",
        )
    return team_id


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
