from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional
import uuid

from app.db.session import get_db
from app.api.auth import get_current_user, decrypt_token
from app.core.config import get_settings
from app.core.security import encrypt_token
from app.models.user import User, Team, TeamMember
from app.models.social_account import SocialAccount
import httpx

router = APIRouter()
settings = get_settings()


class SocialAccountResponse(BaseModel):
    id: uuid.UUID
    platform: str
    account_id: str
    username: str | None
    display_name: str | None
    avatar_url: str | None
    status: str
    scopes: list[str]
    token_expires_at: str | None
    created_at: str

    class Config:
        from_attributes = True


class ConnectResponse(BaseModel):
    authorization_url: str


@router.get("", response_model=list[SocialAccountResponse])
async def list_accounts(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()
    if not team:
        return []

    result = await db.execute(
        select(SocialAccount).where(SocialAccount.team_id == team.id)
    )
    accounts = result.scalars().all()

    return [
        SocialAccountResponse(
            id=a.id,
            platform=a.platform,
            account_id=a.account_id,
            username=a.username,
            display_name=a.display_name,
            avatar_url=a.avatar_url,
            status=a.status,
            scopes=a.scopes,
            token_expires_at=a.token_expires_at.isoformat() if a.token_expires_at else None,
            created_at=a.created_at.isoformat(),
        )
        for a in accounts
    ]


@router.post("/connect/{platform}", response_model=ConnectResponse)
async def connect_account(
    platform: str,
    team_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Verify team membership
    result = await db.execute(
        select(TeamMember).where(TeamMember.team_id == team_id, TeamMember.user_id == current_user.id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Not a member of this team")

    # Redirect to auth endpoint
    from app.api.auth import linkedin_client, twitter_client, facebook_client, instagram_client

    redirect_uri = getattr(settings, f"{platform.upper()}_REDIRECT_URI")
    client = getattr(globals(), f"{platform}_client", None)

    if not client:
        raise HTTPException(status_code=400, detail="Unsupported platform")

    scopes = {
        "linkedin": ["r_liteprofile", "r_emailaddress", "w_member_social"],
        "twitter": ["tweet.read", "tweet.write", "users.read"],
        "facebook": ["pages_show_list", "pages_read_engagement", "pages_manage_posts"],
        "instagram": ["instagram_basic", "instagram_content_publish"],
    }.get(platform, [])

    authorization_url = await client.get_authorization_url(
        redirect_uri,
        state=str(team_id),
        scope=scopes,
    )

    return ConnectResponse(authorization_url=authorization_url)


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_account(
    account_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SocialAccount).join(Team).join(TeamMember)
        .where(
            SocialAccount.id == account_id,
            TeamMember.user_id == current_user.id,
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    await db.delete(account)
    await db.commit()


@router.post("/{account_id}/refresh")
async def refresh_account_token(
    account_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SocialAccount).join(Team).join(TeamMember)
        .where(
            SocialAccount.id == account_id,
            TeamMember.user_id == current_user.id,
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    if not account.refresh_token_enc:
        raise HTTPException(status_code=400, detail="No refresh token available")

    # TODO: Implement actual token refresh using platform OAuth client
    # For now, just return success
    return {"message": "Token refresh initiated"}


@router.get("/{account_id}/validate")
async def validate_account(
    account_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SocialAccount).join(Team).join(TeamMember)
        .where(
            SocialAccount.id == account_id,
            TeamMember.user_id == current_user.id,
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    # TODO: Actually validate token by making API call to platform
    return {"valid": account.status == "active", "status": account.status}


from app.models.user import User