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
from app.services.browser_profile import BrowserProfileError, BrowserProfileService
from app.services.facebook_api import FacebookAPIClient, FacebookAPIError
from app.services.instagram_private_api import (
    InstagramPrivateAPIClient,
    InstagramPrivateAPIError,
)
from app.services.secret_store import secret_store
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
    """Instagram private API login request.

    If username/password are omitted, the SecretStore is consulted
    (INSTAGRAM_USERNAME / INSTAGRAM_PASSWORD).
    """
    username: str | None = None
    password: str | None = None
    verification_code: str | None = None


class BrowserLoginRequest(BaseModel):
    """Facebook/LinkedIn browser automation login request.

    If username/password are omitted, the SecretStore is consulted
    (FACEBOOK_USERNAME / FACEBOOK_PASSWORD or LINKEDIN_USERNAME / LINKEDIN_PASSWORD).
    """
    username: str | None = None
    password: str | None = None
    verification_code: str | None = None


class LoginResponse(BaseModel):
    """Unified login response for private API or browser sessions."""
    session_id: str | None = None
    storage_state: dict | None = None
    logged_in: bool
    two_factor_required: bool = False
    challenge_required: bool = False
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


@router.post("/{account_id}/login", response_model=LoginResponse)
async def platform_login(
    account_id: uuid.UUID,
    req: InstagramLoginRequest | BrowserLoginRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Log in to a platform's private API or browser session.

    - Instagram: username + password for aiograpi-rest sidecar
    - Facebook / LinkedIn: username + password for browser automation

    The resulting session is stored in the account's meta_data for reuse.
    """
    account = await _get_account(account_id, current_user, db)

    if account.platform == "instagram":
        username = (
            req.username
            or await secret_store.get(f"INSTAGRAM_USERNAME_{account.id}")
            or await secret_store.get("INSTAGRAM_USERNAME")
        )
        password = (
            req.password
            or await secret_store.get(f"INSTAGRAM_PASSWORD_{account.id}")
            or await secret_store.get("INSTAGRAM_PASSWORD")
        )
        if not username or not password:
            raise HTTPException(
                status_code=401,
                detail=(
                    "Instagram username and password are required. "
                    "Provide them in the request or set INSTAGRAM_USERNAME / "
                    "INSTAGRAM_PASSWORD (or per-account "
                    f"INSTAGRAM_USERNAME_{account.id} / INSTAGRAM_PASSWORD_{account.id}) "
                    "in the secret store."
                ),
            )
        client = _get_instagram_private_client()
        proxy = await secret_store.get("INSTAGRAM_PROXY")
        try:
            result = await client.login(
                username=username,
                password=password,
                verification_code=getattr(req, "verification_code", None),
                proxy=proxy or None,
            )
        except InstagramPrivateAPIError as e:
            raise HTTPException(status_code=e.status_code, detail=e.detail)

        session_id = result.get("session_id")
        two_factor = result.get("two_factor_required", False)
        challenge = result.get("challenge_required", False)

        if session_id and not two_factor and not challenge:
            meta = _get_meta(account)
            meta["private_api_session_id"] = session_id
            # Also save settings for session restore
            try:
                settings = await client.get_settings(session_id)
                meta["private_api_settings"] = settings
            except Exception:
                pass
            account.meta_data = meta
            await db.commit()

        return LoginResponse(
            session_id=session_id,
            logged_in=bool(session_id) and not two_factor and not challenge,
            two_factor_required=two_factor,
            message=result.get(
                "message",
                "Login successful" if session_id
                else "Challenge required" if challenge
                else "Login failed",
            ),
        )

    if account.platform in ("facebook", "linkedin"):
        if account.platform == "facebook":
            username = (
                req.username
                or await secret_store.get(f"FACEBOOK_USERNAME_{account.id}")
                or await secret_store.get("FACEBOOK_USERNAME")
            )
            password = (
                req.password
                or await secret_store.get(f"FACEBOOK_PASSWORD_{account.id}")
                or await secret_store.get("FACEBOOK_PASSWORD")
            )
        else:
            username = (
                req.username
                or await secret_store.get(f"LINKEDIN_USERNAME_{account.id}")
                or await secret_store.get("LINKEDIN_USERNAME")
            )
            password = (
                req.password
                or await secret_store.get(f"LINKEDIN_PASSWORD_{account.id}")
                or await secret_store.get("LINKEDIN_PASSWORD")
            )

        if not username or not password:
            raise HTTPException(
                status_code=401,
                detail=(
                    f"{account.platform.capitalize()} username and password are required. "
                    f"Provide them in the request or set "
                    f"{account.platform.upper()}_USERNAME / "
                    f"{account.platform.upper()}_PASSWORD (or per-account "
                    f"{account.platform.upper()}_USERNAME_{account.id} / "
                    f"{account.platform.upper()}_PASSWORD_{account.id}) "
                    f"in the secret store."
                ),
            )

        service = BrowserProfileService()
        try:
            if account.platform == "facebook":
                result = await service.login_facebook(
                    username, password, verification_code=getattr(req, "verification_code", None)
                )
            else:
                result = await service.login_linkedin(
                    username, password, verification_code=getattr(req, "verification_code", None)
                )
        except BrowserProfileError as e:
            raise HTTPException(status_code=e.status_code, detail=e.detail)

        storage_state = result.get("storage_state")
        logged_in = result.get("success", False) and not result.get("two_factor_required", False)

        if logged_in:
            meta = _get_meta(account)
            meta["browser_storage_state"] = storage_state
            account.meta_data = meta
            await db.commit()

        return LoginResponse(
            storage_state=storage_state,
            logged_in=logged_in,
            two_factor_required=result.get("two_factor_required", False),
            message=result.get("message", "Login successful" if logged_in else "Login failed"),
        )

    raise HTTPException(
        status_code=400,
        detail=f"Private API or browser login not supported for platform: {account.platform}",
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


# ── Facebook personal (browser automation) ───────────────────────────────────


def _get_facebook_browser_storage(account: SocialAccount) -> dict:
    meta = _get_meta(account)
    storage = meta.get("browser_storage_state")
    if not storage:
        raise HTTPException(
            status_code=401,
            detail="Facebook browser session not found. Call POST /login first.",
        )
    return storage


async def _get_facebook_user_profile(account: SocialAccount) -> ProfileResponse:
    storage = _get_facebook_browser_storage(account)
    service = BrowserProfileService()
    try:
        data = await service.get_facebook_profile(storage)
    except BrowserProfileError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return ProfileResponse(
        platform="facebook",
        account_id=account.account_id,
        username=data.get("name"),
        full_name=data.get("name"),
        about=data.get("about"),
        profile_pic_url=data.get("profile_pic_url"),
        raw=data,
    )


async def _update_facebook_user_profile(
    account: SocialAccount,
    updates: ProfileUpdateRequest,
) -> ProfileUpdateResponse:
    storage = _get_facebook_browser_storage(account)
    service = BrowserProfileService()
    updated: list[str] = []
    ignored: list[str] = []

    try:
        if updates.about is not None:
            result = await service.update_facebook_about(storage, updates.about)
            if result.get("success"):
                updated.append("about")
    except BrowserProfileError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)

    for field in ["headline", "biography", "full_name", "website", "location", "phone", "email", "quotes", "work", "education"]:
        if getattr(updates, field, None) is not None:
            ignored.append(field)

    if not updated:
        return ProfileUpdateResponse(
            success=False,
            message="No supported fields to update for Facebook personal profile",
            ignored_fields=ignored,
        )

    return ProfileUpdateResponse(
        success=True,
        updated_fields=updated,
        ignored_fields=ignored,
        message="Facebook personal profile updated",
    )


async def _upload_facebook_user_picture(
    account: SocialAccount,
    image_bytes: bytes,
) -> ProfileUpdateResponse:
    storage = _get_facebook_browser_storage(account)
    service = BrowserProfileService()
    try:
        await service.update_facebook_profile_picture(storage, image_bytes)
    except BrowserProfileError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return ProfileUpdateResponse(
        success=True,
        updated_fields=["profile_picture"],
        message="Facebook personal profile picture updated",
    )


async def _upload_facebook_user_cover(
    account: SocialAccount,
    image_bytes: bytes,
) -> ProfileUpdateResponse:
    raise HTTPException(
        status_code=501,
        detail="Facebook personal cover photo upload is not yet implemented.",
    )


# ── LinkedIn (browser automation) ────────────────────────────────────────────


def _get_linkedin_browser_storage(account: SocialAccount) -> dict:
    meta = _get_meta(account)
    storage = meta.get("browser_storage_state")
    if not storage:
        raise HTTPException(
            status_code=401,
            detail="LinkedIn browser session not found. Call POST /login first.",
        )
    return storage


async def _get_linkedin_profile(account: SocialAccount) -> ProfileResponse:
    storage = _get_linkedin_browser_storage(account)
    service = BrowserProfileService()
    try:
        data = await service.get_linkedin_profile(storage)
    except BrowserProfileError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return ProfileResponse(
        platform="linkedin",
        account_id=account.account_id,
        username=data.get("name"),
        full_name=data.get("name"),
        headline=data.get("headline"),
        about=data.get("about"),
        profile_pic_url=data.get("profile_pic_url"),
        raw=data,
    )


async def _update_linkedin_profile(
    account: SocialAccount,
    updates: ProfileUpdateRequest,
) -> ProfileUpdateResponse:
    storage = _get_linkedin_browser_storage(account)
    service = BrowserProfileService()
    updated: list[str] = []
    ignored: list[str] = []

    try:
        if updates.headline is not None:
            await service.update_linkedin_headline(storage, updates.headline)
            updated.append("headline")
        if updates.about is not None:
            await service.update_linkedin_about(storage, updates.about)
            updated.append("about")
    except BrowserProfileError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)

    for field in ["biography", "full_name", "website", "location", "phone", "email", "quotes", "work", "education"]:
        if getattr(updates, field, None) is not None:
            ignored.append(field)

    if not updated:
        return ProfileUpdateResponse(
            success=False,
            message="No supported fields to update for LinkedIn",
            ignored_fields=ignored,
        )

    return ProfileUpdateResponse(
        success=True,
        updated_fields=updated,
        ignored_fields=ignored,
        message="LinkedIn profile updated",
    )


async def _upload_linkedin_picture(
    account: SocialAccount,
    image_bytes: bytes,
) -> ProfileUpdateResponse:
    raise HTTPException(
        status_code=501,
        detail="LinkedIn profile picture upload is not yet implemented.",
    )


async def _upload_linkedin_cover(
    account: SocialAccount,
    image_bytes: bytes,
) -> ProfileUpdateResponse:
    raise HTTPException(
        status_code=501,
        detail="LinkedIn cover photo upload is not yet implemented.",
    )


# ── Twitter/X (tweepy v1.1 API) ──────────────────────────────────────────────


async def _get_twitter_service() -> TwitterProfileService:
    """Build a TwitterProfileService from SecretStore, falling back to settings."""
    api_key = await secret_store.get("TWITTER_API_KEY") or settings.TWITTER_API_KEY
    api_secret = await secret_store.get("TWITTER_API_SECRET") or settings.TWITTER_API_SECRET
    access_token = await secret_store.get("TWITTER_ACCESS_TOKEN") or settings.TWITTER_ACCESS_TOKEN
    access_token_secret = await secret_store.get("TWITTER_ACCESS_TOKEN_SECRET") or settings.TWITTER_ACCESS_TOKEN_SECRET
    if not api_key or not access_token:
        raise HTTPException(
            status_code=503,
            detail="Twitter v1.1 API credentials not configured. "
            "Set TWITTER_API_KEY, TWITTER_API_SECRET, TWITTER_ACCESS_TOKEN, "
            "TWITTER_ACCESS_TOKEN_SECRET in the secret store or .env. "
            "Profile writes require Twitter Basic ($100/mo) or Pro tier.",
        )
    return TwitterProfileService(
        api_key=api_key,
        api_secret=api_secret,
        access_token=access_token,
        access_token_secret=access_token_secret,
    )


async def _get_twitter_profile(account: SocialAccount) -> ProfileResponse:
    service = await _get_twitter_service()
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
    service = await _get_twitter_service()
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
    service = await _get_twitter_service()
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
    service = await _get_twitter_service()
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


async def _get_tiktok_service() -> TikTokProfileService:
    """Build a TikTokProfileService from SecretStore, falling back to settings."""
    api_key = await secret_store.get("TIKTOK_PRIVATE_API_KEY") or settings.TIKTOK_PRIVATE_API_KEY
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="TikTok private API key not configured. "
            "Set TIKTOK_PRIVATE_API_KEY in the secret store or .env. "
            "Get a signing server API key at tiktok-private-api.com.",
        )
    return TikTokProfileService(api_key=api_key)


async def _get_tiktok_profile(account: SocialAccount) -> ProfileResponse:
    service = await _get_tiktok_service()
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
    service = await _get_tiktok_service()
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
    service = await _get_tiktok_service()
    try:
        service.upload_avatar(image_bytes)
    except TikTokProfileError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return ProfileUpdateResponse(
        success=True,
        updated_fields=["avatar"],
        message="TikTok profile picture updated",
    )
