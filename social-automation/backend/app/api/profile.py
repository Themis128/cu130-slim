"""Unified profile management API.

This router exposes a single set of endpoints for reading and updating
social media profile metadata (bio, avatar, cover, name, website, etc.)
across all supported platforms.

Platform dispatch:
    - Instagram   → aiograpi-rest sidecar (private mobile API)
    - Facebook Page → existing FacebookAPIClient (Graph API)
    - Facebook user → browser automation (Phase 4)
    - LinkedIn     → browser automation (Phase 4)
    - Twitter/X    → tweepy v1.1 API (Phase 2)
    - TikTok       → tiktok-private-api (Phase 3)

Endpoints:
    GET  /api/v1/profile/{account_id}           — get current profile
    PUT  /api/v1/profile/{account_id}           — update profile fields
    POST /api/v1/profile/{account_id}/picture   — upload profile picture
    POST /api/v1/profile/{account_id}/cover     — upload cover photo
    POST /api/v1/profile/{account_id}/login     — platform login (Instagram private API)
"""

from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.core.config import settings
from app.core.security import decrypt_token
from app.db.session import get_db
from app.models.social_account import SocialAccount
from app.models.user import Team, TeamMember
from app.services.facebook_api import FacebookAPIClient, FacebookAPIError
from app.services.instagram_private_api import (
    InstagramPrivateAPIClient,
    InstagramPrivateAPIError,
)
from app.services.tiktok_profile import TikTokProfileError, TikTokProfileService
from app.services.twitter_profile import TwitterProfileError, TwitterProfileService

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Request / response models ────────────────────────────────────────────────


class WorkEntry(BaseModel):
    """A single work experience entry."""
    employer: str
    position: str
    start_date: str | None = None
    end_date: str | None = None
    description: str | None = None


class EducationEntry(BaseModel):
    """A single education entry."""
    school: str
    degree: str | None = None
    start_year: str | None = None
    end_year: str | None = None


class ProfileUpdateRequest(BaseModel):
    """Unified profile update request.

    Only fields that are set will be updated.  Not all platforms support
    all fields — the service layer will ignore unsupported fields per
    platform and return a list of ignored/unsupported fields.
    """
    about: str | None = None
    headline: str | None = None       # LinkedIn headline
    biography: str | None = None      # Instagram bio
    full_name: str | None = None      # Display name
    website: str | None = None
    location: str | None = None
    phone: str | None = None
    email: str | None = None
    quotes: str | None = None         # Facebook quotes
    work: list[WorkEntry] | None = None
    education: list[EducationEntry] | None = None


class ProfileResponse(BaseModel):
    """Unified profile response."""
    platform: str
    account_id: str
    username: str | None = None
    full_name: str | None = None
    about: str | None = None
    biography: str | None = None
    headline: str | None = None
    website: str | None = None
    location: str | None = None
    phone: str | None = None
    email: str | None = None
    profile_pic_url: str | None = None
    cover_url: str | None = None
    followers: int | None = None
    is_private: bool | None = None
    is_verified: bool | None = None
    raw: dict = {}


class ProfileUpdateResponse(BaseModel):
    """Result of a profile update."""
    success: bool
    updated_fields: list[str] = []
    ignored_fields: list[str] = []
    message: str = ""


class InstagramLoginRequest(BaseModel):
    """Instagram private API login request."""
    username: str
    password: str
    verification_code: str | None = None


class InstagramLoginResponse(BaseModel):
    """Instagram private API login response."""
    session_id: str | None = None
    logged_in: bool
    two_factor_required: bool = False
    message: str = ""


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _get_account(
    account_id: uuid.UUID,
    current_user,
    db: AsyncSession,
) -> SocialAccount:
    """Fetch any social account owned by the current user's team."""
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
    return account


def _get_meta(account: SocialAccount) -> dict:
    """Parse the meta_data JSON column."""
    if isinstance(account.meta_data, dict):
        return account.meta_data
    if isinstance(account.meta_data, str):
        try:
            return json.loads(account.meta_data)
        except Exception:
            return {}
    return {}


def _get_instagram_private_client() -> InstagramPrivateAPIClient:
    return InstagramPrivateAPIClient(settings.INSTAGRAM_PRIVATE_API_URL)


def _get_instagram_session_id(account: SocialAccount) -> str | None:
    """Retrieve a stored Instagram private API session ID."""
    meta = _get_meta(account)
    return meta.get("private_api_session_id")


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/{account_id}", response_model=ProfileResponse)
async def get_profile(
    account_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the current profile metadata for any connected account."""
    account = await _get_account(account_id, current_user, db)

    if account.platform == "instagram":
        return await _get_instagram_profile(account)
    elif account.platform == "facebook" and account.is_business:
        return await _get_facebook_page_profile(account)
    elif account.platform == "facebook":
        return await _get_facebook_user_profile(account)
    elif account.platform == "linkedin":
        return await _get_linkedin_profile(account)
    elif account.platform == "twitter":
        return await _get_twitter_profile(account)
    elif account.platform == "tiktok":
        return await _get_tiktok_profile(account)
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Profile reads not supported for platform: {account.platform}",
        )


@router.put("/{account_id}", response_model=ProfileUpdateResponse)
async def update_profile(
    account_id: uuid.UUID,
    updates: ProfileUpdateRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update profile fields for any connected account."""
    account = await _get_account(account_id, current_user, db)

    if account.platform == "instagram":
        return await _update_instagram_profile(account, updates, db)
    elif account.platform == "facebook" and account.is_business:
        return await _update_facebook_page_profile(account, updates)
    elif account.platform == "facebook":
        return await _update_facebook_user_profile(account, updates)
    elif account.platform == "linkedin":
        return await _update_linkedin_profile(account, updates)
    elif account.platform == "twitter":
        return await _update_twitter_profile(account, updates)
    elif account.platform == "tiktok":
        return await _update_tiktok_profile(account, updates)
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Profile updates not supported for platform: {account.platform}",
        )


@router.post("/{account_id}/picture", response_model=ProfileUpdateResponse)
async def upload_profile_picture(
    account_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    file: UploadFile = File(...),
):
    """Upload a new profile picture for any connected account."""
    account = await _get_account(account_id, current_user, db)
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="No image file provided")

    if account.platform == "instagram":
        return await _upload_instagram_picture(account, image_bytes, db)
    elif account.platform == "facebook" and account.is_business:
        return await _upload_facebook_page_picture(account, image_bytes)
    elif account.platform == "facebook":
        return await _upload_facebook_user_picture(account, image_bytes)
    elif account.platform == "linkedin":
        return await _upload_linkedin_picture(account, image_bytes)
    elif account.platform == "twitter":
        return await _upload_twitter_picture(account, image_bytes)
    elif account.platform == "tiktok":
        return await _upload_tiktok_picture(account, image_bytes)
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Profile picture upload not supported for platform: {account.platform}",
        )


@router.post("/{account_id}/cover", response_model=ProfileUpdateResponse)
async def upload_cover_photo(
    account_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    file: UploadFile = File(...),
):
    """Upload a new cover photo for any connected account."""
    account = await _get_account(account_id, current_user, db)
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="No image file provided")

    if account.platform == "facebook" and account.is_business:
        return await _upload_facebook_page_cover(account, image_bytes)
    elif account.platform == "facebook":
        return await _upload_facebook_user_cover(account, image_bytes)
    elif account.platform == "linkedin":
        return await _upload_linkedin_cover(account, image_bytes)
    elif account.platform == "twitter":
        return await _upload_twitter_banner(account, image_bytes)
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Cover photo upload not supported for platform: {account.platform}",
        )


@router.post("/{account_id}/login", response_model=InstagramLoginResponse)
async def platform_login(
    account_id: uuid.UUID,
    req: InstagramLoginRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Log in to a platform's private API.

    Currently only Instagram (via aiograpi-rest sidecar) is supported.
    The session ID is stored in the account's meta_data for reuse.
    """
    account = await _get_account(account_id, current_user, db)

    if account.platform != "instagram":
        raise HTTPException(
            status_code=400,
            detail=f"Private API login not supported for platform: {account.platform}",
        )

    client = _get_instagram_private_client()
    try:
        result = await client.login(
            username=req.username,
            password=req.password,
            verification_code=req.verification_code,
        )
    except InstagramPrivateAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)

    session_id = result.get("session_id")
    two_factor = result.get("two_factor_required", False)

    if session_id and not two_factor:
        # Persist session_id in meta_data
        meta = _get_meta(account)
        meta["private_api_session_id"] = session_id
        account.meta_data = meta
        await db.commit()

    return InstagramLoginResponse(
        session_id=session_id,
        logged_in=bool(session_id) and not two_factor,
        two_factor_required=two_factor,
        message=result.get("message", "Login successful" if session_id else "Login failed"),
    )


# ── Instagram (private API) ──────────────────────────────────────────────────


async def _get_instagram_profile(account: SocialAccount) -> ProfileResponse:
    session_id = _get_instagram_session_id(account)
    if not session_id:
        raise HTTPException(
            status_code=401,
            detail="Instagram private API session not found. Call POST /login first.",
        )
    client = _get_instagram_private_client()
    try:
        data = await client.get_account(session_id)
    except InstagramPrivateAPIError as e:
        if e.status_code == 401:
            raise HTTPException(
                status_code=401,
                detail="Instagram session expired. Call POST /login to re-authenticate.",
            )
        raise HTTPException(status_code=e.status_code, detail=e.detail)

    return ProfileResponse(
        platform="instagram",
        account_id=account.account_id,
        username=data.get("username"),
        full_name=data.get("full_name"),
        biography=data.get("biography"),
        website=data.get("external_url"),
        phone=data.get("phone_number"),
        email=data.get("email"),
        profile_pic_url=data.get("profile_pic_url"),
        is_private=data.get("is_private"),
        is_verified=data.get("is_verified"),
        raw=data,
    )


async def _update_instagram_profile(
    account: SocialAccount,
    updates: ProfileUpdateRequest,
    db: AsyncSession,
) -> ProfileUpdateResponse:
    session_id = _get_instagram_session_id(account)
    if not session_id:
        raise HTTPException(
            status_code=401,
            detail="Instagram private API session not found. Call POST /login first.",
        )

    client = _get_instagram_private_client()
    updated: list[str] = []
    ignored: list[str] = []

    # Map unified fields to Instagram fields
    kwargs: dict[str, str] = {}
    if updates.biography is not None:
        kwargs["biography"] = updates.biography
    if updates.about is not None:
        kwargs["biography"] = updates.about  # about maps to biography on Instagram
    if updates.full_name is not None:
        kwargs["full_name"] = updates.full_name
    if updates.website is not None:
        kwargs["external_url"] = updates.website
    if updates.phone is not None:
        kwargs["phone_number"] = updates.phone
    if updates.email is not None:
        kwargs["email"] = updates.email

    # Fields Instagram doesn't support
    for field in ["headline", "location", "quotes", "work", "education"]:
        if getattr(updates, field, None) is not None:
            ignored.append(field)

    if not kwargs:
        return ProfileUpdateResponse(
            success=False,
            message="No supported fields to update for Instagram",
            ignored_fields=ignored,
        )

    try:
        await client.update_account(session_id, **kwargs)
        updated = list(kwargs.keys())
    except InstagramPrivateAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)

    return ProfileUpdateResponse(
        success=True,
        updated_fields=updated,
        ignored_fields=ignored,
        message="Instagram profile updated",
    )


async def _upload_instagram_picture(
    account: SocialAccount,
    image_bytes: bytes,
    db: AsyncSession,
) -> ProfileUpdateResponse:
    session_id = _get_instagram_session_id(account)
    if not session_id:
        raise HTTPException(
            status_code=401,
            detail="Instagram private API session not found. Call POST /login first.",
        )
    client = _get_instagram_private_client()
    try:
        await client.update_profile_picture(session_id, image_bytes)
    except InstagramPrivateAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return ProfileUpdateResponse(
        success=True,
        updated_fields=["profile_picture"],
        message="Instagram profile picture updated",
    )


# ── Facebook Page (Graph API — existing) ─────────────────────────────────────


async def _get_facebook_page_profile(account: SocialAccount) -> ProfileResponse:
    token = decrypt_token(account.access_token_enc)
    client = FacebookAPIClient(access_token=token, page_id=account.account_id)
    try:
        info = await client.get_page_info()
    except FacebookAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    return ProfileResponse(
        platform="facebook",
        account_id=account.account_id,
        username=info.get("username") or info.get("name"),
        full_name=info.get("name"),
        about=info.get("about"),
        website=info.get("website"),
        phone=info.get("phone"),
        profile_pic_url=info.get("picture", {}).get("data", {}).get("url") if isinstance(info.get("picture"), dict) else None,
        raw=info,
    )


async def _update_facebook_page_profile(
    account: SocialAccount,
    updates: ProfileUpdateRequest,
) -> ProfileUpdateResponse:
    token = decrypt_token(account.access_token_enc)
    client = FacebookAPIClient(access_token=token, page_id=account.account_id)
    updated: list[str] = []
    ignored: list[str] = []

    kwargs: dict[str, str | None] = {}
    if updates.about is not None:
        kwargs["about"] = updates.about
    if updates.website is not None:
        kwargs["website"] = updates.website
    if updates.phone is not None:
        kwargs["phone"] = updates.phone

    for field in ["headline", "biography", "full_name", "location", "email", "quotes", "work", "education"]:
        if getattr(updates, field, None) is not None:
            ignored.append(field)

    if not kwargs:
        return ProfileUpdateResponse(
            success=False,
            message="No supported fields to update for Facebook Page",
            ignored_fields=ignored,
        )

    try:
        await client.update_page_info(**kwargs)
        updated = list(kwargs.keys())
    except FacebookAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return ProfileUpdateResponse(
        success=True,
        updated_fields=updated,
        ignored_fields=ignored,
        message="Facebook Page profile updated",
    )


async def _upload_facebook_page_picture(
    account: SocialAccount,
    image_bytes: bytes,
) -> ProfileUpdateResponse:
    token = decrypt_token(account.access_token_enc)
    client = FacebookAPIClient(access_token=token, page_id=account.account_id)
    try:
        await client.upload_profile_picture(image_bytes)
    except FacebookAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    return ProfileUpdateResponse(
        success=True,
        updated_fields=["profile_picture"],
        message="Facebook Page profile picture updated",
    )


async def _upload_facebook_page_cover(
    account: SocialAccount,
    image_bytes: bytes,
) -> ProfileUpdateResponse:
    token = decrypt_token(account.access_token_enc)
    client = FacebookAPIClient(access_token=token, page_id=account.account_id)
    try:
        await client.upload_cover_photo(image_bytes)
    except FacebookAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    return ProfileUpdateResponse(
        success=True,
        updated_fields=["cover_photo"],
        message="Facebook Page cover photo updated",
    )


# ── Facebook personal (browser automation — Phase 4) ─────────────────────────


async def _get_facebook_user_profile(account: SocialAccount) -> ProfileResponse:
    raise HTTPException(
        status_code=501,
        detail="Facebook personal profile reads require browser automation (Phase 4). "
        "Use the Facebook Page profile editor for Page accounts.",
    )


async def _update_facebook_user_profile(
    account: SocialAccount,
    updates: ProfileUpdateRequest,
) -> ProfileUpdateResponse:
    raise HTTPException(
        status_code=501,
        detail="Facebook personal profile updates require browser automation (Phase 4).",
    )


async def _upload_facebook_user_picture(
    account: SocialAccount,
    image_bytes: bytes,
) -> ProfileUpdateResponse:
    raise HTTPException(
        status_code=501,
        detail="Facebook personal profile picture upload requires browser automation (Phase 4).",
    )


async def _upload_facebook_user_cover(
    account: SocialAccount,
    image_bytes: bytes,
) -> ProfileUpdateResponse:
    raise HTTPException(
        status_code=501,
        detail="Facebook personal cover photo upload requires browser automation (Phase 4).",
    )


# ── LinkedIn (browser automation — Phase 4) ──────────────────────────────────


async def _get_linkedin_profile(account: SocialAccount) -> ProfileResponse:
    raise HTTPException(
        status_code=501,
        detail="LinkedIn profile reads require browser automation (Phase 4).",
    )


async def _update_linkedin_profile(
    account: SocialAccount,
    updates: ProfileUpdateRequest,
) -> ProfileUpdateResponse:
    raise HTTPException(
        status_code=501,
        detail="LinkedIn profile updates require browser automation (Phase 4).",
    )


async def _upload_linkedin_picture(
    account: SocialAccount,
    image_bytes: bytes,
) -> ProfileUpdateResponse:
    raise HTTPException(
        status_code=501,
        detail="LinkedIn profile picture upload requires browser automation (Phase 4).",
    )


async def _upload_linkedin_cover(
    account: SocialAccount,
    image_bytes: bytes,
) -> ProfileUpdateResponse:
    raise HTTPException(
        status_code=501,
        detail="LinkedIn cover photo upload requires browser automation (Phase 4).",
    )


# ── Twitter/X (tweepy v1.1 API) ──────────────────────────────────────────────


def _get_twitter_service() -> TwitterProfileService:
    """Build a TwitterProfileService from settings."""
    if not settings.TWITTER_API_KEY or not settings.TWITTER_ACCESS_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="Twitter v1.1 API credentials not configured. "
            "Set TWITTER_API_KEY, TWITTER_API_SECRET, TWITTER_ACCESS_TOKEN, "
            "TWITTER_ACCESS_TOKEN_SECRET in .env. "
            "Profile writes require Twitter Basic ($100/mo) or Pro tier.",
        )
    return TwitterProfileService(
        api_key=settings.TWITTER_API_KEY,
        api_secret=settings.TWITTER_API_SECRET,
        access_token=settings.TWITTER_ACCESS_TOKEN,
        access_token_secret=settings.TWITTER_ACCESS_TOKEN_SECRET,
    )


async def _get_twitter_profile(account: SocialAccount) -> ProfileResponse:
    service = _get_twitter_service()
    try:
        data = service.get_profile()
    except TwitterProfileError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return ProfileResponse(
        platform="twitter",
        account_id=account.account_id,
        username=data.get("username"),
        full_name=data.get("full_name"),
        biography=data.get("biography"),
        location=data.get("location"),
        website=data.get("website"),
        profile_pic_url=data.get("profile_pic_url"),
        cover_url=data.get("cover_url"),
        followers=data.get("followers"),
        is_verified=data.get("is_verified"),
        raw=data.get("raw", {}),
    )


async def _update_twitter_profile(
    account: SocialAccount,
    updates: ProfileUpdateRequest,
) -> ProfileUpdateResponse:
    service = _get_twitter_service()
    updated: list[str] = []
    ignored: list[str] = []

    kwargs: dict[str, str] = {}
    if updates.full_name is not None:
        kwargs["name"] = updates.full_name
    if updates.about is not None or updates.biography is not None:
        kwargs["description"] = updates.about or updates.biography or ""
    if updates.location is not None:
        kwargs["location"] = updates.location
    if updates.website is not None:
        kwargs["url"] = updates.website

    for field in ["headline", "phone", "email", "quotes", "work", "education"]:
        if getattr(updates, field, None) is not None:
            ignored.append(field)

    if not kwargs:
        return ProfileUpdateResponse(
            success=False,
            message="No supported fields to update for Twitter",
            ignored_fields=ignored,
        )

    try:
        result = service.update_profile(**kwargs)
        updated = result.get("updated_fields", list(kwargs.keys()))
    except TwitterProfileError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)

    return ProfileUpdateResponse(
        success=True,
        updated_fields=updated,
        ignored_fields=ignored,
        message="Twitter profile updated",
    )


async def _upload_twitter_picture(
    account: SocialAccount,
    image_bytes: bytes,
) -> ProfileUpdateResponse:
    service = _get_twitter_service()
    try:
        service.update_profile_image(image_bytes)
    except TwitterProfileError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return ProfileUpdateResponse(
        success=True,
        updated_fields=["profile_picture"],
        message="Twitter profile picture updated",
    )


async def _upload_twitter_banner(
    account: SocialAccount,
    image_bytes: bytes,
) -> ProfileUpdateResponse:
    service = _get_twitter_service()
    try:
        service.update_profile_banner(image_bytes)
    except TwitterProfileError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return ProfileUpdateResponse(
        success=True,
        updated_fields=["banner"],
        message="Twitter banner updated",
    )


# ── TikTok (private API via tiktokflow) ──────────────────────────────────────


def _get_tiktok_service() -> TikTokProfileService:
    """Build a TikTokProfileService from settings."""
    if not settings.TIKTOK_PRIVATE_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="TikTok private API key not configured. "
            "Set TIKTOK_PRIVATE_API_KEY in .env. "
            "Get a signing server API key at tiktok-private-api.com.",
        )
    return TikTokProfileService(api_key=settings.TIKTOK_PRIVATE_API_KEY)


async def _get_tiktok_profile(account: SocialAccount) -> ProfileResponse:
    service = _get_tiktok_service()
    try:
        data = service.get_profile()
    except TikTokProfileError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return ProfileResponse(
        platform="tiktok",
        account_id=account.account_id,
        username=data.get("username"),
        full_name=data.get("full_name"),
        biography=data.get("biography"),
        profile_pic_url=data.get("profile_pic_url"),
        followers=data.get("followers"),
        is_verified=data.get("is_verified"),
        is_private=data.get("is_private"),
        raw=data.get("raw", {}),
    )


async def _update_tiktok_profile(
    account: SocialAccount,
    updates: ProfileUpdateRequest,
) -> ProfileUpdateResponse:
    service = _get_tiktok_service()
    updated: list[str] = []
    ignored: list[str] = []

    try:
        if updates.full_name is not None:
            service.update_nickname(updates.full_name)
            updated.append("nickname")
        if updates.about is not None or updates.biography is not None:
            service.update_signature(updates.about or updates.biography or "")
            updated.append("signature")
    except TikTokProfileError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)

    for field in ["headline", "website", "location", "phone", "email", "quotes", "work", "education"]:
        if getattr(updates, field, None) is not None:
            ignored.append(field)

    if not updated:
        return ProfileUpdateResponse(
            success=False,
            message="No supported fields to update for TikTok",
            ignored_fields=ignored,
        )

    return ProfileUpdateResponse(
        success=True,
        updated_fields=updated,
        ignored_fields=ignored,
        message="TikTok profile updated",
    )


async def _upload_tiktok_picture(
    account: SocialAccount,
    image_bytes: bytes,
) -> ProfileUpdateResponse:
    service = _get_tiktok_service()
    try:
        service.upload_avatar(image_bytes)
    except TikTokProfileError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return ProfileUpdateResponse(
        success=True,
        updated_fields=["avatar"],
        message="TikTok profile picture updated",
    )
