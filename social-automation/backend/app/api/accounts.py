import base64
import hashlib
import json
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import (
    LINKEDIN_SCOPES,
    facebook_client,
    get_current_user,
    instagram_client,
    linkedin_client,
    threads_client,
    tiktok_client,
    twitter_client,
)
from app.core.config import get_settings
from app.db.session import get_db
from app.models.social_account import SocialAccount
from app.models.user import Team, TeamMember, User

router = APIRouter()
settings = get_settings()


def _pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for S256 PKCE."""
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


def _encode_state(team_id: uuid.UUID, code_verifier: str | None = None) -> str:
    if code_verifier is None:
        return str(team_id)
    payload = json.dumps({"t": str(team_id), "cv": code_verifier})
    return base64.urlsafe_b64encode(payload.encode()).decode()


def _decode_state(state: str) -> tuple[uuid.UUID, str | None]:
    try:
        data = json.loads(base64.urlsafe_b64decode(state + "==").decode())
        return uuid.UUID(data["t"]), data.get("cv")
    except Exception:
        return uuid.UUID(state), None


PLATFORM_CLIENTS = {
    "linkedin": linkedin_client,
    "twitter": twitter_client,
    "facebook": facebook_client,
    "instagram": instagram_client,
    "threads": threads_client,
    "tiktok": tiktok_client,
}


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
    account_type: str = "person"  # person | organization
    meta_data: dict = {}

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
            account_type=(a.meta_data or {}).get("account_type", "person"),
            meta_data=a.meta_data or {},
        )
        for a in accounts
    ]


class ConnectBodyRequest(BaseModel):
    platform: str
    team_id: uuid.UUID | None = None

    @field_validator("team_id", mode="before")
    @classmethod
    def coerce_team_id(cls, v):
        if v is None:
            return None
        try:
            return uuid.UUID(str(v))
        except (ValueError, AttributeError):
            return None


@router.post("/connect", response_model=ConnectResponse)
async def connect_account_body(
    data: ConnectBodyRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Body-based alias for /connect/{platform} — used by the frontend."""
    # Auto-resolve team from current user if not provided
    team_result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = team_result.scalars().first()
    if not team:
        raise HTTPException(status_code=403, detail="No team found for user")
    team_id = data.team_id or team.id

    redirect_uri = getattr(settings, f"{data.platform.upper()}_REDIRECT_URI", None)
    client = PLATFORM_CLIENTS.get(data.platform)

    if not client or not redirect_uri:
        raise HTTPException(status_code=400, detail=f"Unsupported platform: {data.platform}")

    scopes = {
        # w_organization_social required to post as a LinkedIn Company Page (e.g. cloudless.gr)
        "linkedin": LINKEDIN_SCOPES,
        "twitter": ["tweet.read", "tweet.write", "users.read", "offline.access"],
        "facebook": ["pages_show_list", "pages_read_engagement", "pages_manage_posts"],
        "instagram": ["instagram_basic", "instagram_content_publish", "pages_show_list"],
        "threads": ["threads_basic", "threads_content_publish"],
        "tiktok": ["user.info.basic", "video.publish", "video.upload"],
    }.get(data.platform, [])

    # Twitter OAuth 2.0 requires PKCE
    code_verifier: str | None = None
    code_challenge: str | None = None
    if data.platform == "twitter":
        code_verifier, code_challenge = _pkce_pair()

    state = _encode_state(team_id, code_verifier)
    authorization_url = await client.get_authorization_url(
        redirect_uri,
        state=state,
        scope=scopes,
        code_challenge=code_challenge,
        code_challenge_method="S256" if code_challenge else None,
    )
    return ConnectResponse(authorization_url=authorization_url)


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

    redirect_uri = getattr(settings, f"{platform.upper()}_REDIRECT_URI")
    client = PLATFORM_CLIENTS.get(platform)

    if not client:
        raise HTTPException(status_code=400, detail="Unsupported platform")

    scopes = {
        "linkedin": LINKEDIN_SCOPES,
        "twitter": ["tweet.read", "tweet.write", "users.read"],
        "facebook": ["pages_show_list", "pages_read_engagement", "pages_manage_posts"],
        "instagram": ["instagram_basic", "instagram_content_publish", "pages_show_list"],
    }.get(platform, [])

    authorization_url = await client.get_authorization_url(
        redirect_uri,
        state=str(team_id),
        scope=scopes,
    )

    return ConnectResponse(authorization_url=authorization_url)


@router.post("/linkedin/sync-organizations")
async def sync_linkedin_organizations(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Re-scan LinkedIn company pages for the connected personal account.

    Requires the token to include ``w_organization_social`` / ``r_organization_social``.
    Reconnect LinkedIn from Accounts if those scopes are missing.
    """
    import httpx

    from app.api.auth import _sync_linkedin_organizations
    from app.core.security import decrypt_token

    team_result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = team_result.scalars().first()
    if not team:
        raise HTTPException(status_code=403, detail="No team found for user")

    result = await db.execute(
        select(SocialAccount).where(
            SocialAccount.team_id == team.id,
            SocialAccount.platform == "linkedin",
            SocialAccount.status == "active",
        )
    )
    accounts = result.scalars().all()
    person = next(
        (a for a in accounts if (a.meta_data or {}).get("account_type", "person") == "person"),
        accounts[0] if accounts else None,
    )
    if not person:
        raise HTTPException(status_code=400, detail="Connect a LinkedIn personal account first")

    token = decrypt_token(person.access_token_enc)
    refresh = decrypt_token(person.refresh_token_enc) if person.refresh_token_enc else None
    async with httpx.AsyncClient(timeout=60.0) as http:
        synced = await _sync_linkedin_organizations(
            db=db,
            team_id=team.id,
            access_token=token,
            refresh_token=refresh,
            scopes=person.scopes or [],
            http=http,
        )
    await db.commit()
    return {
        "synced": [
            {
                "id": str(a.id),
                "account_id": a.account_id,
                "display_name": a.display_name,
                "username": a.username,
                "account_type": (a.meta_data or {}).get("account_type"),
            }
            for a in synced
        ],
        "hint": (
            "Reconnect LinkedIn with company-page scopes if this list is empty "
            "(Community Management API + w_organization_social on your LinkedIn app)."
            if not synced
            else None
        ),
    }


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


