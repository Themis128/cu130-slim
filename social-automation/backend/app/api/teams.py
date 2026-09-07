"""Team management API — multi-team CRUD, membership, and role enforcement.

Phase 1.1 of the SaaS readiness plan.  All endpoints are team-scoped and use
the centralized dependencies in ``app.api.deps`` for role checks.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentUser,
    DbSession,
)
from app.core.security import create_access_token
from app.models.user import Team, TeamMember, User, UserRole

router = APIRouter()


# ── Role hierarchy ────────────────────────────────────────────────────────────

_ROLE_LEVEL: dict[UserRole, int] = {
    UserRole.VIEWER: 0,
    UserRole.EDITOR: 1,
    UserRole.ADMIN: 2,
    UserRole.OWNER: 3,
}


def _role_at_least(role: UserRole, minimum: UserRole) -> bool:
    return _ROLE_LEVEL.get(role, 0) >= _ROLE_LEVEL[minimum]


# ── Pydantic schemas ──────────────────────────────────────────────────────────


class TeamCreate(BaseModel):
    name: str


class TeamUpdate(BaseModel):
    name: str


class MemberResponse(BaseModel):
    user_id: uuid.UUID
    email: str
    name: str | None
    role: UserRole

    class Config:
        from_attributes = True


class TeamResponse(BaseModel):
    id: uuid.UUID
    name: str
    owner_id: uuid.UUID
    member_count: int
    role: UserRole

    class Config:
        from_attributes = True


class TeamDetailResponse(BaseModel):
    id: uuid.UUID
    name: str
    owner_id: uuid.UUID
    members: list[MemberResponse]


class InviteRequest(BaseModel):
    email: EmailStr
    role: UserRole = UserRole.EDITOR


class AddMemberRequest(BaseModel):
    role: UserRole = UserRole.EDITOR


class ChangeRoleRequest(BaseModel):
    role: UserRole


class InviteResponse(BaseModel):
    invited: bool
    email: str
    token: str | None = None
    message: str


class AcceptInviteRequest(BaseModel):
    token: str


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _get_team_or_404(db: AsyncSession, team_id: uuid.UUID) -> Team:
    result = await db.execute(select(Team).where(Team.id == team_id))
    team = result.scalar_one_or_none()
    if team is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return team


async def _get_membership(
    db: AsyncSession, team_id: uuid.UUID, user_id: uuid.UUID
) -> TeamMember | None:
    result = await db.execute(
        select(TeamMember).where(
            TeamMember.team_id == team_id,
            TeamMember.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def _count_members(db: AsyncSession, team_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.count()).select_from(TeamMember).where(TeamMember.team_id == team_id)
    )
    return result.scalar_one()


async def _assert_membership(
    db: AsyncSession, team_id: uuid.UUID, user_id: uuid.UUID
) -> TeamMember:
    membership = await _get_membership(db, team_id, user_id)
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this team",
        )
    return membership


async def _assert_role(
    db: AsyncSession, team_id: uuid.UUID, user_id: uuid.UUID, minimum: UserRole
) -> TeamMember:
    membership = await _assert_membership(db, team_id, user_id)
    if not _role_at_least(membership.role, minimum):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Requires {minimum.value} role or higher in this team",
        )
    return membership


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("", response_model=list[TeamResponse])
async def list_teams(
    current_user: CurrentUser,
    db: DbSession,
):
    """List all teams the current user belongs to, with role and member count."""
    teams_result = await db.execute(
        select(Team, TeamMember.role)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .where(TeamMember.user_id == current_user.id)
    )
    rows = teams_result.all()
    responses: list[TeamResponse] = []
    for team, role in rows:
        count = await _count_members(db, team.id)
        responses.append(
            TeamResponse(
                id=team.id,
                name=team.name,
                owner_id=team.owner_id,
                member_count=count,
                role=role,
            )
        )
    return responses


@router.post("", response_model=TeamResponse, status_code=status.HTTP_201_CREATED)
async def create_team(
    data: TeamCreate,
    current_user: CurrentUser,
    db: DbSession,
):
    """Create a new team. The creator becomes the OWNER."""
    team = Team(name=data.name, owner_id=current_user.id)
    db.add(team)
    await db.flush()

    membership = TeamMember(team_id=team.id, user_id=current_user.id, role=UserRole.OWNER)
    db.add(membership)

    await db.commit()
    await db.refresh(team)

    return TeamResponse(
        id=team.id,
        name=team.name,
        owner_id=team.owner_id,
        member_count=1,
        role=UserRole.OWNER,
    )


@router.get("/{team_id}", response_model=TeamDetailResponse)
async def get_team(
    team_id: uuid.UUID,
    current_user: CurrentUser,
    db: DbSession,
):
    """Get team details and list of members. Must be a member."""
    team = await _get_team_or_404(db, team_id)
    await _assert_membership(db, team.id, current_user.id)

    members_result = await db.execute(
        select(TeamMember, User)
        .join(User, User.id == TeamMember.user_id)
        .where(TeamMember.team_id == team.id)
        .order_by(TeamMember.role)
    )
    members = [
        MemberResponse(
            user_id=user.id,
            email=user.email,
            name=user.name,
            role=membership.role,
        )
        for membership, user in members_result.all()
    ]
    return TeamDetailResponse(
        id=team.id,
        name=team.name,
        owner_id=team.owner_id,
        members=members,
    )


@router.patch("/{team_id}", response_model=TeamDetailResponse)
async def update_team(
    team_id: uuid.UUID,
    data: TeamUpdate,
    current_user: CurrentUser,
    db: DbSession,
):
    """Update team name. ADMIN+ only."""
    team = await _get_team_or_404(db, team_id)
    await _assert_role(db, team.id, current_user.id, UserRole.ADMIN)

    team.name = data.name
    await db.commit()
    await db.refresh(team)

    members_result = await db.execute(
        select(TeamMember, User)
        .join(User, User.id == TeamMember.user_id)
        .where(TeamMember.team_id == team.id)
        .order_by(TeamMember.role)
    )
    members = [
        MemberResponse(
            user_id=user.id,
            email=user.email,
            name=user.name,
            role=membership.role,
        )
        for membership, user in members_result.all()
    ]
    return TeamDetailResponse(
        id=team.id,
        name=team.name,
        owner_id=team.owner_id,
        members=members,
    )


@router.delete("/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_team(
    team_id: uuid.UUID,
    current_user: CurrentUser,
    db: DbSession,
):
    """Delete a team. OWNER only. Cannot delete if other members exist."""
    team = await _get_team_or_404(db, team_id)
    await _assert_role(db, team.id, current_user.id, UserRole.OWNER)

    # Count members — only the OWNER (themselves) may be present.
    count = await _count_members(db, team.id)
    if count > 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete a team that still has other members. Remove them first.",
        )

    await db.delete(team)
    await db.commit()


@router.post("/{team_id}/invite", response_model=InviteResponse)
async def invite_member(
    team_id: uuid.UUID,
    data: InviteRequest,
    current_user: CurrentUser,
    db: DbSession,
):
    """Invite a user by email.

    If the user already exists, they are added as a member immediately.
    If not, a signed invite token is returned so the frontend can build a
    join-link.
    """
    team = await _get_team_or_404(db, team_id)
    await _assert_role(db, team.id, current_user.id, UserRole.ADMIN)

    # Resolve frontend URL for invite links (used in both branches below).
    from app.core.config import get_settings

    settings = get_settings()
    frontend_url = ""
    for origin in settings.CORS_ORIGINS:
        if "8082" in origin or "3000" in origin or "3001" in origin or "cloudless" in origin:
            frontend_url = origin.rstrip("/")
            break
    if not frontend_url and settings.CORS_ORIGINS:
        frontend_url = settings.CORS_ORIGINS[0].rstrip("/")

    # Look up the user by email.
    user_result = await db.execute(select(User).where(User.email == data.email))
    target_user = user_result.scalar_one_or_none()

    if target_user is not None:
        # User exists — add as member (idempotent).
        existing = await _get_membership(db, team.id, target_user.id)
        if existing is not None:
            # Update role if different.
            if existing.role != data.role:
                existing.role = data.role
                await db.commit()
            return InviteResponse(
                invited=True,
                email=data.email,
                message=f"{data.email} is already a member of this team",
            )
        membership = TeamMember(
            team_id=team.id, user_id=target_user.id, role=data.role
        )
        db.add(membership)
        await db.commit()

        # Send a "you've been added" notification if the user opted in.
        prefs = target_user.notification_preferences or {}
        if prefs.get("email_on_invite", True):
            try:
                import asyncio

                from app.services.email_templates import send_team_added_email

                inviter_name = current_user.name or current_user.email
                asyncio.create_task(
                    send_team_added_email(
                        inviter_name, target_user.email, team.name,
                        f"{frontend_url}/team",
                    )
                )
            except Exception:
                pass  # non-fatal

        return InviteResponse(
            invited=True,
            email=data.email,
            message=f"{data.email} added to the team as {data.role.value}",
        )

    # User does not exist — issue an invite token.
    token = create_access_token(
        {
            "sub": str(current_user.id),
            "invite_team_id": str(team.id),
            "invite_email": data.email,
            "invite_role": data.role.value,
        }
    )

    invite_link = f"{frontend_url}/auth/accept-invite?token={token}"

    inviter_name = current_user.name or current_user.email
    try:
        import asyncio

        from app.services.email_templates import send_team_invite_email

        asyncio.create_task(
            send_team_invite_email(inviter_name, data.email, team.name, invite_link)
        )
    except Exception:
        pass  # non-fatal — email logging happens inside the template

    return InviteResponse(
        invited=False,
        email=data.email,
        token=token,
        message=f"Invitation token generated for {data.email}",
    )


@router.post("/{team_id}/members/{user_id}", response_model=MemberResponse, status_code=status.HTTP_201_CREATED)
async def add_member(
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    data: AddMemberRequest,
    current_user: CurrentUser,
    db: DbSession,
):
    """Add an existing user as a member. ADMIN+ only."""
    team = await _get_team_or_404(db, team_id)
    await _assert_role(db, team.id, current_user.id, UserRole.ADMIN)

    user_result = await db.execute(select(User).where(User.id == user_id))
    target_user = user_result.scalar_one_or_none()
    if target_user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    existing = await _get_membership(db, team.id, target_user.id)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is already a member of this team",
        )

    membership = TeamMember(team_id=team.id, user_id=target_user.id, role=data.role)
    db.add(membership)
    await db.commit()
    await db.refresh(membership)

    return MemberResponse(
        user_id=target_user.id,
        email=target_user.email,
        name=target_user.name,
        role=membership.role,
    )


@router.patch("/{team_id}/members/{user_id}", response_model=MemberResponse)
async def change_member_role(
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    data: ChangeRoleRequest,
    current_user: CurrentUser,
    db: DbSession,
):
    """Change a member's role.

    ADMIN+ can change EDITOR/VIEWER roles.
    OWNER can change anyone (including promoting to ADMIN or transferring ownership).
    """
    team = await _get_team_or_404(db, team_id)
    actor_membership = await _assert_membership(db, team.id, current_user.id)

    target_membership = await _get_membership(db, team.id, user_id)
    if target_membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    # Permission rules:
    # - OWNER can change anyone.
    # - ADMIN can change EDITOR/VIEWER (not other ADMINs or the OWNER).
    if actor_membership.role != UserRole.OWNER:
        if not _role_at_least(actor_membership.role, UserRole.ADMIN):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Requires admin role or higher to change member roles",
            )
        if target_membership.role == UserRole.OWNER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot change the role of the team owner",
            )
        if target_membership.role == UserRole.ADMIN and actor_membership.role == UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admins cannot change the role of other admins",
            )
        # ADMIN cannot promote to OWNER.
        if data.role == UserRole.OWNER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the owner can transfer ownership",
            )

    target_membership.role = data.role

    # If ownership is being transferred, update the team owner_id too.
    if data.role == UserRole.OWNER:
        team.owner_id = target_membership.user_id

    await db.commit()
    await db.refresh(target_membership)

    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one()

    return MemberResponse(
        user_id=user.id,
        email=user.email,
        name=user.name,
        role=target_membership.role,
    )


@router.delete("/{team_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    current_user: CurrentUser,
    db: DbSession,
):
    """Remove a member from the team.

    ADMIN+ can remove EDITOR/VIEWER.
    OWNER can remove anyone.
    An OWNER cannot remove themselves if no other OWNER exists.
    """
    team = await _get_team_or_404(db, team_id)
    actor_membership = await _assert_membership(db, team.id, current_user.id)

    target_membership = await _get_membership(db, team.id, user_id)
    if target_membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    # Permission rules.
    if actor_membership.role != UserRole.OWNER:
        if not _role_at_least(actor_membership.role, UserRole.ADMIN):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Requires admin role or higher to remove members",
            )
        if target_membership.role in (UserRole.OWNER, UserRole.ADMIN) and actor_membership.role == UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admins cannot remove the owner or other admins",
            )

    # Prevent an OWNER from removing themselves if they are the only OWNER.
    if (
        target_membership.role == UserRole.OWNER
        and target_membership.user_id == current_user.id
    ):
        owners_result = await db.execute(
            select(func.count())
            .select_from(TeamMember)
            .where(TeamMember.team_id == team.id, TeamMember.role == UserRole.OWNER)
        )
        owner_count = owners_result.scalar_one()
        if owner_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot remove the only owner. Transfer ownership first.",
            )

    await db.delete(target_membership)
    await db.commit()


# ── Accept invite (no team-scoped auth — uses invite JWT) ──────────────


@router.post("/accept-invite", response_model=TeamResponse)
async def accept_invite(
    data: AcceptInviteRequest,
    current_user: CurrentUser,
    db: DbSession,
):
    """Accept a team invitation by presenting the invite JWT.

    The invite token (created by ``POST /{team_id}/invite`` when the user
    does not yet exist) carries ``invite_team_id``, ``invite_email``, and
    ``invite_role``.  This endpoint verifies the token, checks that the
    logged-in user's email matches the invite email, and adds the user
    as a member of the target team.
    """
    from app.core.security import decode_token

    payload = decode_token(data.token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired invite token",
        )

    invite_email = payload.get("invite_email")
    invite_team_id = payload.get("invite_team_id")
    invite_role = payload.get("invite_role", "editor")

    if not invite_email or not invite_team_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token is not a valid invite token",
        )

    if current_user.email != invite_email:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This invite was sent to a different email address",
        )

    try:
        team_uuid = uuid.UUID(invite_team_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid team ID in invite token",
        )

    team = await _get_team_or_404(db, team_uuid)

    # Check if already a member.
    existing = await _get_membership(db, team.id, current_user.id)
    if existing is not None:
        # Update role if different.
        try:
            new_role = UserRole(invite_role)
        except ValueError:
            new_role = UserRole.EDITOR
        if existing.role != new_role:
            existing.role = new_role
            await db.commit()
    else:
        try:
            new_role = UserRole(invite_role)
        except ValueError:
            new_role = UserRole.EDITOR
        membership = TeamMember(
            team_id=team.id, user_id=current_user.id, role=new_role
        )
        db.add(membership)
        await db.commit()

    # Return team info with the user's role.
    member_count_result = await db.execute(
        select(func.count()).select_from(TeamMember).where(TeamMember.team_id == team.id)
    )
    member_count = member_count_result.scalar_one()

    final_membership = await _get_membership(db, team.id, current_user.id)
    return TeamResponse(
        id=team.id,
        name=team.name,
        owner_id=team.owner_id,
        member_count=member_count,
        role=final_membership.role if final_membership else UserRole.VIEWER,
    )
