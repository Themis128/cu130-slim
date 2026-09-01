import base64
import hashlib
import json
import secrets
import uuid

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
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
from app.core.security import decrypt_token, encrypt_token
from app.db.session import get_db
from app.models.social_account import SocialAccount
from app.models.user import Team, TeamMember, User
from app.services.facebook_api import FacebookAPIClient, FacebookAPIError
from app.services.instagram_api import InstagramAPIClient, InstagramAPIError
from app.services.linkedin_api import LinkedInAPIError
from app.services.threads_api import ThreadsAPIClient, ThreadsAPIError
from app.services.tiktok_api import TikTokAPIClient, TikTokAPIError
from app.services.twitter_api import TwitterAPIClient, TwitterAPIError

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
    account_type: str = "person"  # person | organization | page | business | creator | user
    is_business: bool = False
    parent_account_id: str | None = None
    meta_data: dict = {}

    class Config:
        from_attributes = True


class ConnectResponse(BaseModel):
    authorization_url: str


@router.get("", response_model=list[SocialAccountResponse])
async def list_accounts(
    platform: str | None = None,
    account_type: str | None = None,
    is_business: bool | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()
    if not team:
        return []

    query = select(SocialAccount).where(SocialAccount.team_id == team.id)
    if platform:
        query = query.where(SocialAccount.platform == platform)
    if account_type:
        query = query.where(SocialAccount.account_type == account_type)
    if is_business is not None:
        query = query.where(SocialAccount.is_business == is_business)
    query = query.order_by(SocialAccount.platform, SocialAccount.is_business.desc(), SocialAccount.created_at.desc())

    result = await db.execute(query)
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
            account_type=a.account_type,
            is_business=a.is_business,
            parent_account_id=str(a.parent_account_id) if a.parent_account_id else None,
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
        "threads": ["threads_basic", "threads_content_publish", "threads_manage_insights", "threads_manage_replies"],
        "tiktok": ["user.info.basic", "video.publish", "video.upload"],
    }.get(data.platform, [])

    # Twitter and TikTok OAuth 2.0 require PKCE
    code_verifier: str | None = None
    code_challenge: str | None = None
    if data.platform in ("twitter", "tiktok"):
        code_verifier, code_challenge = _pkce_pair()

    state = _encode_state(team_id, code_verifier)

    # TikTok requires client_key and comma-separated scopes in the authorize URL
    extra_params: dict = {}
    if data.platform == "tiktok":
        extra_params["client_key"] = settings.TIKTOK_CLIENT_KEY
        extra_params["scope"] = ",".join(scopes)
        scopes = None  # Don't let the library join with spaces

    authorization_url = await client.get_authorization_url(
        redirect_uri,
        state=state,
        scope=scopes,
        code_challenge=code_challenge,
        code_challenge_method="S256" if code_challenge else None,
        extras_params=extra_params if extra_params else None,
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
        "twitter": ["tweet.read", "tweet.write", "users.read", "offline.access"],
        "facebook": ["pages_show_list", "pages_read_engagement", "pages_manage_posts"],
        "instagram": ["instagram_basic", "instagram_content_publish", "pages_show_list"],
        "threads": ["threads_basic", "threads_content_publish", "threads_manage_insights", "threads_manage_replies"],
        "tiktok": ["user.info.basic", "video.publish", "video.upload"],
    }.get(platform, [])

    # TikTok requires client_key and comma-separated scopes in the authorize URL
    extra_params: dict = {}
    tiktok_scopes: list[str] | None = scopes
    if platform == "tiktok":
        extra_params["client_key"] = settings.TIKTOK_CLIENT_KEY
        extra_params["scope"] = ",".join(scopes)
        tiktok_scopes = None  # Don't let the library join with spaces

    # Twitter and TikTok require PKCE
    code_verifier: str | None = None
    code_challenge: str | None = None
    if platform in ("twitter", "tiktok"):
        code_verifier, code_challenge = _pkce_pair()

    state = _encode_state(team_id, code_verifier)

    authorization_url = await client.get_authorization_url(
        redirect_uri,
        state=state,
        scope=tiktok_scopes,
        code_challenge=code_challenge,
        code_challenge_method="S256" if code_challenge else None,
        extras_params=extra_params if extra_params else None,
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
    from app.api.auth import _sync_linkedin_organizations

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


@router.get("/{account_id}", response_model=SocialAccountResponse)
async def get_account(
    account_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single social account by ID."""
    result = await db.execute(
        select(SocialAccount).join(Team).join(TeamMember)
        .where(
            SocialAccount.id == account_id,
            TeamMember.user_id == current_user.id,
        )
    )
    a = result.scalar_one_or_none()
    if not a:
        raise HTTPException(status_code=404, detail="Account not found")
    return SocialAccountResponse(
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


@router.post("/{account_id}/test")
async def test_account(
    account_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Test that an account's token is still valid by calling the platform API."""
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


    token = decrypt_token(account.access_token_enc)
    platform = account.platform

    try:
        valid = False
        if platform == "linkedin":
            from app.services.linkedin_api import LinkedInAPIClient
            client = LinkedInAPIClient(access_token=token)
            await client.validate_token()
            valid = True
        elif platform == "twitter":
            client = TwitterAPIClient(access_token=token)
            await client.validate_token()
            valid = True
        elif platform == "facebook":
            client = FacebookAPIClient(access_token=token, page_id=account.account_id)
            await client.validate_token()
            valid = True
        elif platform == "instagram":
            client = InstagramAPIClient(access_token=token, ig_user_id=account.account_id)
            await client.validate_token()
            valid = True
        elif platform == "threads":
            client = ThreadsAPIClient(access_token=token, user_id=account.account_id)
            await client.validate_token()
            valid = True
        elif platform == "tiktok":
            client = TikTokAPIClient(access_token=token, open_id=account.account_id)
            await client.validate_token()
            valid = True
        else:
            return {"valid": account.status == "active", "status": account.status, "tested": False, "message": f"No test endpoint for {platform}"}

        if valid and account.status != "active":
            account.status = "active"
            await db.commit()
        return {"valid": True, "status": "active", "tested": True, "message": "Token is valid"}
    except (LinkedInAPIError, TwitterAPIError, FacebookAPIError, InstagramAPIError, ThreadsAPIError, TikTokAPIError) as exc:
        if exc.status_code in (401, 403):
            if account.status == "active":
                account.status = "expired"
                await db.commit()
            return {"valid": False, "status": "expired", "tested": True, "message": "Token is expired or invalid"}
        return {
            "valid": account.status == "active",
            "status": account.status,
            "tested": True,
            "message": f"Platform returned HTTP {exc.status_code}: {exc.response_text[:200]}",
        }
    except Exception as exc:
        return {"valid": account.status == "active", "status": account.status, "tested": False, "message": f"Network error: {exc}"}


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

    from app.core.security import decrypt_token

    refresh_token = decrypt_token(account.refresh_token_enc)
    platform = account.platform

    # Map platform to its OAuth client
    client_map = {
        "linkedin": linkedin_client,
        "twitter": twitter_client,
        "facebook": facebook_client,
        "instagram": instagram_client,
        "threads": threads_client,
        "tiktok": tiktok_client,
    }
    client = client_map.get(platform)
    if client is None:
        raise HTTPException(status_code=400, detail=f"Token refresh not supported for {platform}")

    redirect_uri = getattr(settings, f"{platform.upper()}_REDIRECT_URI", None)
    if not redirect_uri:
        raise HTTPException(status_code=500, detail=f"Redirect URI not configured for {platform}")

    try:
        token = await client.refresh_token(refresh_token)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Token refresh failed for {platform}: {exc}",
        ) from exc

    new_access = token.get("access_token", "")
    new_refresh = token.get("refresh_token")
    if not new_access:
        raise HTTPException(status_code=400, detail="No access_token in refresh response")

    account.access_token_enc = encrypt_token(new_access)
    if new_refresh:
        account.refresh_token_enc = encrypt_token(new_refresh)
    account.status = "active"
    await db.commit()

    return {"message": "Token refreshed", "status": "active"}


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


    token = decrypt_token(account.access_token_enc)
    platform = account.platform

    try:
        if platform == "linkedin":
            from app.services.linkedin_api import LinkedInAPIClient
            client = LinkedInAPIClient(access_token=token)
            await client.validate_token()
        elif platform == "twitter":
            client = TwitterAPIClient(access_token=token)
            await client.validate_token()
        elif platform == "facebook":
            client = FacebookAPIClient(access_token=token, page_id=account.account_id)
            await client.validate_token()
        elif platform == "instagram":
            client = InstagramAPIClient(access_token=token, ig_user_id=account.account_id)
            await client.validate_token()
        elif platform == "threads":
            client = ThreadsAPIClient(access_token=token, user_id=account.account_id)
            await client.validate_token()
        elif platform == "tiktok":
            client = TikTokAPIClient(access_token=token, open_id=account.account_id)
            await client.validate_token()
        else:
            return {"valid": account.status == "active", "status": account.status, "checked": False}

        if account.status != "active":
            account.status = "active"
            await db.commit()
        return {"valid": True, "status": "active", "checked": True}
    except (LinkedInAPIError, TwitterAPIError, FacebookAPIError, InstagramAPIError, ThreadsAPIError, TikTokAPIError) as exc:
        if exc.status_code in (401, 403):
            if account.status == "active":
                account.status = "expired"
                await db.commit()
            return {"valid": False, "status": "expired", "checked": True}
        return {"valid": account.status == "active", "status": account.status, "checked": True, "http_status": exc.status_code}
    except Exception:
        return {"valid": account.status == "active", "status": account.status, "checked": False}


# ── Business account discovery ─────────────────────────────────────────────────

class BusinessAccountSyncResponse(BaseModel):
    platform: str
    synced: int
    accounts: list[SocialAccountResponse]


@router.post("/{account_id}/sync-business-accounts", response_model=BusinessAccountSyncResponse)
async def sync_business_accounts(
    account_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Discover and store business accounts/pages for the connected platform account."""
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

    from app.core.security import decrypt_token

    token = decrypt_token(account.access_token_enc)
    synced_accounts: list[SocialAccountResponse] = []

    if account.platform == "facebook":
        client = FacebookAPIClient(access_token=token, page_id=account.account_id)
        pages = await client.get_pages()

        for page in pages:
            page_id = page["id"]
            page_token = page.get("access_token") or token
            existing = await db.execute(
                select(SocialAccount).where(
                    SocialAccount.team_id == account.team_id,
                    SocialAccount.platform == "facebook",
                    SocialAccount.account_id == page_id,
                )
            )
            existing_account = existing.scalar_one_or_none()
            meta = {
                "account_type": "page",
                "page_token": page_token,
                "category": page.get("category"),
                "tasks": page.get("tasks", []),
            }
            if existing_account:
                existing_account.access_token_enc = encrypt_token(page_token)
                existing_account.status = "active"
                existing_account.username = page.get("name")
                existing_account.display_name = page.get("name")
                existing_account.is_business = True
                existing_account.account_type = "page"
                existing_account.parent_account_id = account.id
                existing_account.meta_data = {**existing_account.meta_data, **meta}
            else:
                new_account = SocialAccount(
                    team_id=account.team_id,
                    platform="facebook",
                    account_id=page_id,
                    username=page.get("name"),
                    display_name=page.get("name"),
                    access_token_enc=encrypt_token(page_token),
                    status="active",
                    account_type="page",
                    is_business=True,
                    parent_account_id=account.id,
                    meta_data=meta,
                )
                db.add(new_account)
            await db.flush()

        result = await db.execute(
            select(SocialAccount).where(
                SocialAccount.team_id == account.team_id,
                SocialAccount.platform == "facebook",
                SocialAccount.is_business.is_(True),
            )
        )
        synced_accounts = result.scalars().all()

    elif account.platform == "linkedin":
        from app.api.auth import _sync_linkedin_organizations
        from app.services.linkedin_api import LinkedInAPIClient

        li_client = LinkedInAPIClient(access_token=token)
        synced = await _sync_linkedin_organizations(
            db=db,
            team_id=account.team_id,
            access_token=token,
            refresh_token=None,
            scopes=account.scopes,
            http=li_client._client,
        )
        synced_accounts = synced

    elif account.platform == "instagram":
        client = FacebookAPIClient(access_token=token, page_id=account.account_id)
        pages = await client.get_pages()

        for page in pages:
            ig = page.get("instagram_business_account")
            if not ig:
                continue
            ig_user_id = str(ig["id"])
            page_token = page.get("access_token") or token
            existing = await db.execute(
                select(SocialAccount).where(
                    SocialAccount.team_id == account.team_id,
                    SocialAccount.platform == "instagram",
                    SocialAccount.account_id == ig_user_id,
                )
            )
            existing_account = existing.scalar_one_or_none()
            meta = {
                "account_type": "business",
                "ig_business_id": ig_user_id,
                "username": ig.get("username"),
                "profile_picture_url": ig.get("profile_picture_url"),
                "page_id": page["id"],
            }
            if existing_account:
                existing_account.access_token_enc = encrypt_token(page_token)
                existing_account.status = "active"
                existing_account.username = ig.get("username")
                existing_account.display_name = ig.get("name") or ig.get("username")
                existing_account.is_business = True
                existing_account.account_type = "business"
                existing_account.parent_account_id = account.id
                existing_account.meta_data = {**existing_account.meta_data, **meta}
            else:
                new_account = SocialAccount(
                    team_id=account.team_id,
                    platform="instagram",
                    account_id=ig_user_id,
                    username=ig.get("username"),
                    display_name=ig.get("name") or ig.get("username"),
                    access_token_enc=encrypt_token(page_token),
                    status="active",
                    account_type="business",
                    is_business=True,
                    parent_account_id=account.id,
                    meta_data=meta,
                )
                db.add(new_account)
            await db.flush()

        result = await db.execute(
            select(SocialAccount).where(
                SocialAccount.team_id == account.team_id,
                SocialAccount.platform == "instagram",
                SocialAccount.is_business.is_(True),
            )
        )
        synced_accounts = result.scalars().all()

    else:
        raise HTTPException(
            status_code=400,
            detail=f"Business account sync not supported for {account.platform}",
        )

    await db.commit()

    return BusinessAccountSyncResponse(
        platform=account.platform,
        synced=len(synced_accounts),
        accounts=[
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
                account_type=a.account_type,
                is_business=a.is_business,
                parent_account_id=str(a.parent_account_id) if a.parent_account_id else None,
                meta_data=a.meta_data or {},
            )
            for a in synced_accounts
        ],
    )


@router.post("/{account_id}/set-business-account")
async def set_business_account(
    account_id: uuid.UUID,
    business_account_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Select a discovered business account as the active publishing target."""
    result = await db.execute(
        select(SocialAccount).join(Team).join(TeamMember)
        .where(
            SocialAccount.id == account_id,
            TeamMember.user_id == current_user.id,
        )
    )
    parent = result.scalar_one_or_none()
    if not parent:
        raise HTTPException(status_code=404, detail="Account not found")

    result = await db.execute(
        select(SocialAccount).where(
            SocialAccount.team_id == parent.team_id,
            SocialAccount.platform == parent.platform,
            SocialAccount.account_id == business_account_id,
            SocialAccount.is_business.is_(True),
        )
    )
    business = result.scalar_one_or_none()
    if not business:
        raise HTTPException(status_code=404, detail="Business account not found")

    return {
        "message": f"Selected {parent.platform} business account {business_account_id} for publishing",
        "account_id": str(business.id),
        "platform": business.platform,
        "business_account_id": business.account_id,
    }


# ── Facebook Page profile management ──────────────────────────────────────────


def _get_facebook_page_client(account: SocialAccount) -> FacebookAPIClient:
    """Build a FacebookAPIClient from a Page-type account.

    Page accounts store the permanent Page token in ``access_token_enc``
    and the Page ID in ``account_id``.
    """
    token = decrypt_token(account.access_token_enc)
    page_id = account.account_id
    return FacebookAPIClient(access_token=token, page_id=page_id)


async def _get_facebook_account(
    account_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
    require_page: bool = False,
) -> SocialAccount:
    """Fetch a Facebook account owned by the current user's team."""
    result = await db.execute(
        select(SocialAccount).join(Team).join(TeamMember)
        .where(
            SocialAccount.id == account_id,
            SocialAccount.platform == "facebook",
            TeamMember.user_id == current_user.id,
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Facebook account not found")
    if require_page and not account.is_business:
        raise HTTPException(
            status_code=400,
            detail="This operation requires a Facebook Page account, not a user account. "
            "Use 'Sync Business Accounts' first to discover your Pages.",
        )
    return account


class PageProfileUpdate(BaseModel):
    """Editable Facebook Page profile fields."""
    about: str | None = None
    description: str | None = None
    website: str | None = None
    phone: str | None = None


@router.get("/{account_id}/page-profile")
async def get_facebook_page_profile(
    account_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the current Facebook Page profile metadata (about, description, website, etc.)."""
    account = await _get_facebook_account(account_id, current_user, db, require_page=True)
    client = _get_facebook_page_client(account)
    try:
        info = await client.get_page_info()
    except FacebookAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    # Tasks lookup via /me/accounts requires a user token, not a Page token.
    # Try it but don't fail the whole request if it errors.
    tasks: list[str] = []
    try:
        tasks = await client.get_page_tasks()
    except FacebookAPIError:
        pass
    return {"profile": info, "tasks": tasks}


@router.put("/{account_id}/page-profile")
async def update_facebook_page_profile(
    account_id: uuid.UUID,
    updates: PageProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update Facebook Page profile fields (about, description, website, phone).

    Requires the Page token to have ``pages_manage_metadata`` permission
    and the user to have the ``MANAGE`` task on the Page.
    """
    account = await _get_facebook_account(account_id, current_user, db, require_page=True)
    client = _get_facebook_page_client(account)
    try:
        success = await client.update_page_info(
            about=updates.about,
            description=updates.description,
            website=updates.website,
            phone=updates.phone,
        )
        return {"success": success}
    except FacebookAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{account_id}/page-profile/picture")
async def upload_facebook_profile_picture(
    account_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    file: UploadFile = File(...),
):
    """Upload a new profile picture for the Facebook Page."""
    account = await _get_facebook_account(account_id, current_user, db, require_page=True)
    client = _get_facebook_page_client(account)
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="No image file provided")
    try:
        success = await client.upload_profile_picture(image_bytes)
        return {"success": success}
    except FacebookAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))


@router.post("/{account_id}/page-profile/cover")
async def upload_facebook_cover_photo(
    account_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    file: UploadFile = File(...),
):
    """Upload a new cover photo for the Facebook Page.

    The image must be at least 400px wide. Two-step: upload as unpublished
    photo, then set as cover.
    """
    account = await _get_facebook_account(account_id, current_user, db, require_page=True)
    client = _get_facebook_page_client(account)
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="No image file provided")
    try:
        photo_id = await client.upload_cover_photo(image_bytes)
        return {"success": True, "photo_id": photo_id}
    except FacebookAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))


class AssignManageTaskRequest(BaseModel):
    """Request body for assigning the MANAGE task to the current user."""
    business_id: str
    business_user_id: str | None = None


@router.post("/{account_id}/page-profile/assign-manage-task")
async def assign_facebook_manage_task(
    account_id: uuid.UUID,
    req: AssignManageTaskRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Assign the MANAGE task to the current user on the Facebook Page.

    This is required to update ``about`` and ``description`` fields via
    the Graph API. If ``business_user_id`` is not provided, the endpoint
    looks it up from the Page's assigned users list.
    """
    account = await _get_facebook_account(account_id, current_user, db, require_page=True)
    client = _get_facebook_page_client(account)

    business_user_id = req.business_user_id
    if not business_user_id:
        try:
            users = await client.get_assigned_users(req.business_id)
            if users:
                business_user_id = users[0].get("id")
        except FacebookAPIError as e:
            raise HTTPException(status_code=e.status_code, detail=str(e))

    if not business_user_id:
        raise HTTPException(
            status_code=400,
            detail="Could not determine business_user_id. Provide it in the request body.",
        )

    tasks = ["MANAGE", "CREATE_CONTENT", "MODERATE", "ADVERTISE", "ANALYZE", "MESSAGING"]
    try:
        success = await client.assign_page_tasks(
            business_user_id=business_user_id,
            tasks=tasks,
            business_id=req.business_id,
        )
        return {"success": success, "business_user_id": business_user_id, "tasks": tasks}
    except FacebookAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))

