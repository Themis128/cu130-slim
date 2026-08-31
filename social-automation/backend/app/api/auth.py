import base64
import json
import logging
import uuid
from datetime import UTC

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from httpx_oauth.clients.facebook import FacebookOAuth2
from httpx_oauth.clients.linkedin import LinkedInOAuth2
from httpx_oauth.oauth2 import BaseOAuth2
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.limiter import limiter
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    encrypt_token,
    hash_password,
    verify_password,
)
from app.db.session import get_db
from app.models.social_account import SocialAccount
from app.models.user import Team, TeamMember, User, UserRole

settings = get_settings()

router = APIRouter()
logger = logging.getLogger(__name__)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_PREFIX}/auth/login")

linkedin_client = LinkedInOAuth2(settings.LINKEDIN_CLIENT_ID, settings.LINKEDIN_CLIENT_SECRET)

# Human-readable env var that holds the client id for each OAuth platform.
# Used only for clear error messages when a platform is not configured.
# TikTok's identifier is its "client key" (TIKTOK_CLIENT_KEY), which is passed
# to the client as the client_id positional argument for the authorize URL.
_OAUTH_CLIENT_ID_ENV_VAR: dict[str, str] = {
    "linkedin": "LINKEDIN_CLIENT_ID",
    "facebook": "FACEBOOK_CLIENT_ID",
    "instagram": "INSTAGRAM_CLIENT_ID",
    "instagram2": "INSTAGRAM2_CLIENT_ID",
    "threads": "THREADS_CLIENT_ID",
    "twitter": "TWITTER_CLIENT_ID",
    "tiktok": "TIKTOK_CLIENT_KEY",
}

# LinkedIn OAuth scopes — shared between the authorize request and the callback
# so the two can never drift. Requested from the consent screen on connect.
#   - openid / profile / email       : "Sign In with LinkedIn" product — gives us
#     the v2/userinfo profile + the personal author URN (urn:li:person:{sub}).
#   - w_member_social                : "Share on LinkedIn" product — post as the
#     member's personal profile.
#   - w_organization_social          : "Community Management API" — post as a
#     Company Page (required by the cloudless.gr carousel pipeline).
#   - r_organization_social          : "Community Management API" — read company
#     page data.
#   - r_organization_admin           : Legacy Organizations API — REQUIRED to
#     query /rest/organizationAcls so we can discover the Company Pages the
#     member administers. Do not drop it unless org discovery is intentionally
#     disabled. Keep the requested list small: a degraded or denied consent
#     screen from LinkedIn is frequently caused by requesting a scope (or
#     enabling a Product below) that the app does not actually have approved.
LINKEDIN_SCOPES: list[str] = [
    "openid",
    "profile",
    "email",
    "w_member_social",
    "w_organization_social",
    "r_organization_social",
    "r_organization_admin",
]
# Instagram2 client (Instagram API with Instagram Login)
instagram2_client = BaseOAuth2(
    client_id=settings.INSTAGRAM2_CLIENT_ID,
    client_secret=settings.INSTAGRAM2_CLIENT_SECRET,
    authorize_endpoint="https://www.instagram.com/oauth/authorize",
    access_token_endpoint="https://api.instagram.com/oauth/access_token",
)


async def _sync_linkedin_organizations(
    *,
    db: AsyncSession,
    team_id: uuid.UUID,
    access_token: str,
    refresh_token: str | None,
    scopes: list[str],
    http: httpx.AsyncClient,
) -> list[SocialAccount]:
    """Upsert LinkedIn Company Pages the member can administer / post as."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Linkedin-Version": "202608",
        "X-Restli-Protocol-Version": "2.0.0",
    }
    # Prefer REST organizationAcls; fall back to v2 if needed.
    acl_urls = [
        "https://api.linkedin.com/rest/organizationAcls?q=roleAssignee&role=ADMINISTRATOR&state=APPROVED",
        "https://api.linkedin.com/v2/organizationAcls?q=roleAssignee&role=ADMINISTRATOR&state=APPROVED",
        "https://api.linkedin.com/rest/organizationAcls?q=roleAssignee&state=APPROVED",
    ]
    elements: list[dict] = []
    for url in acl_urls:
        resp = await http.get(url, headers=headers)
        if resp.status_code == 200:
            elements = (resp.json() or {}).get("elements") or []
            if elements:
                break

    synced: list[SocialAccount] = []
    for el in elements:
        org_urn = el.get("organization") or el.get("organizationalTarget") or ""
        if not isinstance(org_urn, str) or "organization:" not in org_urn:
            continue
        org_id = org_urn.rsplit(":", 1)[-1]
        if not org_id:
            continue

        display_name = f"LinkedIn Page {org_id}"
        username = None
        avatar_url = None
        vanity = None
        for org_url in (
            f"https://api.linkedin.com/rest/organizations/{org_id}",
            f"https://api.linkedin.com/v2/organizations/{org_id}",
        ):
            org_resp = await http.get(org_url, headers=headers)
            if org_resp.status_code != 200:
                continue
            org = org_resp.json() or {}
            name_field = org.get("localizedName") or org.get("name")
            if isinstance(name_field, str) and name_field.strip():
                display_name = name_field.strip()
            elif isinstance(name_field, dict):
                localized = name_field.get("localized") if isinstance(name_field.get("localized"), dict) else name_field
                if isinstance(localized, dict) and localized:
                    display_name = str(localized.get("en_US") or next(iter(localized.values())))
            vanity = org.get("vanityName") or vanity
            username = vanity or username
            break

        result = await db.execute(
            select(SocialAccount).where(
                SocialAccount.team_id == team_id,
                SocialAccount.platform == "linkedin",
                SocialAccount.account_id == org_id,
            )
        )
        account = result.scalar_one_or_none()
        meta = {
            "account_type": "organization",
            "author_urn": f"urn:li:organization:{org_id}",
            "vanity_name": vanity,
            "role": el.get("role"),
        }
        if account:
            account.access_token_enc = encrypt_token(access_token)
            if refresh_token:
                account.refresh_token_enc = encrypt_token(refresh_token)
            account.scopes = scopes
            account.status = "active"
            account.username = username
            account.display_name = display_name
            account.avatar_url = avatar_url
            account.account_type = "organization"
            account.is_business = True
            account.meta_data = {**(account.meta_data or {}), **meta}
        else:
            account = SocialAccount(
                team_id=team_id,
                platform="linkedin",
                account_id=org_id,
                username=username,
                display_name=display_name,
                avatar_url=avatar_url,
                access_token_enc=encrypt_token(access_token),
                refresh_token_enc=encrypt_token(refresh_token) if refresh_token else None,
                scopes=scopes,
                status="active",
                account_type="organization",
                is_business=True,
                meta_data=meta,
            )
            db.add(account)
        synced.append(account)

    if synced:
        await db.flush()
    return synced


# Twitter OAuth2 (using BaseOAuth2)
twitter_client: BaseOAuth2 = BaseOAuth2(
    settings.TWITTER_CLIENT_ID,
    settings.TWITTER_CLIENT_SECRET,
    authorize_endpoint="https://twitter.com/i/oauth2/authorize",
    access_token_endpoint="https://api.twitter.com/2/oauth2/token",
    refresh_token_endpoint="https://api.twitter.com/2/oauth2/token",
    base_scopes=["tweet.read", "tweet.write", "users.read", "offline.access"],
    name="twitter",
    token_endpoint_auth_method="client_secret_basic",
)
facebook_client = FacebookOAuth2(settings.FACEBOOK_CLIENT_ID, settings.FACEBOOK_CLIENT_SECRET)
# Threads OAuth 2.0
threads_client: BaseOAuth2 = BaseOAuth2(
    settings.THREADS_CLIENT_ID,
    settings.THREADS_CLIENT_SECRET,
    authorize_endpoint="https://threads.net/oauth/authorize",
    access_token_endpoint="https://graph.threads.net/oauth/access_token",
    refresh_token_endpoint="https://graph.threads.net/oauth/access_token",
    base_scopes=["threads_basic", "threads_content_publish"],
    name="threads",
)
# Instagram via Facebook Graph API (Basic Display API deprecated Dec 2024)
instagram_client = BaseOAuth2(
    settings.INSTAGRAM_CLIENT_ID,
    settings.INSTAGRAM_CLIENT_SECRET,
    authorize_endpoint="https://www.facebook.com/dialog/oauth",
    access_token_endpoint="https://graph.facebook.com/oauth/access_token",
    refresh_token_endpoint="https://graph.facebook.com/oauth/access_token",
    base_scopes=["instagram_basic", "instagram_content_publish", "pages_show_list"],
    name="instagram",
)
# TikTok OAuth 2.0 — uses client_key (not client_id) and custom token endpoint
tiktok_client = BaseOAuth2(
    settings.TIKTOK_CLIENT_KEY,
    settings.TIKTOK_CLIENT_SECRET,
    authorize_endpoint="https://www.tiktok.com/v2/auth/authorize/",
    access_token_endpoint="https://open.tiktokapis.com/v2/oauth/token/",
    refresh_token_endpoint="https://open.tiktokapis.com/v2/oauth/token/",
    base_scopes=["user.info.basic", "video.publish", "video.upload"],
    name="tiktok",
    token_endpoint_auth_method="client_secret_post",
)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    name: str | None = None


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    name: str | None
    avatar_url: str | None
    timezone: str
    two_factor_enabled: bool = False

    class Config:
        from_attributes = True


class RefreshRequest(BaseModel):
    refresh_token: str


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    return user


async def get_current_user_optional(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User | None:
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return None

    token = auth_header.split(" ")[1]
    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        return None

    user_id = payload.get("sub")
    if not user_id:
        return None

    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    return result.scalar_one_or_none()


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def register(request: Request, user_data: UserCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(User).where(User.email == user_data.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")

    user = User(
        email=user_data.email,
        password_hash=hash_password(user_data.password),
        name=user_data.name,
        timezone=settings.APP_TIMEZONE,
    )
    db.add(user)
    await db.flush()

    # Create default team
    team = Team(name=f"{user.name or user.email}'s Team", owner_id=user.id)
    db.add(team)
    await db.flush()

    membership = TeamMember(team_id=team.id, user_id=user.id, role=UserRole.OWNER)
    db.add(membership)

    await db.commit()
    await db.refresh(user)

    return user


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
async def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == form_data.username))
    user = result.scalar_one_or_none()

    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    access_token = create_access_token({"sub": str(user.id), "email": user.email})
    refresh_token = create_refresh_token({"sub": str(user.id)})

    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(request: RefreshRequest, db: AsyncSession = Depends(get_db)):
    payload = decode_token(request.refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    access_token = create_access_token({"sub": str(user.id), "email": user.email})
    refresh_token = create_refresh_token({"sub": str(user.id)})

    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user


class UpdateProfileRequest(BaseModel):
    full_name: str | None = None
    email: str | None = None
    avatar_url: str | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@router.patch("/me", response_model=UserResponse)
async def update_profile(
    data: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if data.full_name is not None:
        current_user.name = data.full_name
    if data.email is not None:
        current_user.email = data.email
    if data.avatar_url is not None:
        current_user.avatar_url = data.avatar_url
    await db.commit()
    await db.refresh(current_user)
    return current_user


@router.post("/change-password")
async def change_password(
    data: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.core.security import hash_password, verify_password
    if not verify_password(data.current_password, current_user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
    current_user.password_hash = hash_password(data.new_password)
    await db.commit()
    return {"message": "Password updated"}


# ── 2FA (TOTP) ────────────────────────────────────────────────────────────────

class TwoFactorSetupResponse(BaseModel):
    secret: str
    qr_uri: str


class TwoFactorVerifyRequest(BaseModel):
    code: str


@router.post("/2fa/setup", response_model=TwoFactorSetupResponse)
async def setup_2fa(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a TOTP secret and QR URI for 2FA setup."""
    import base64
    import os

    # Generate a random secret (20 bytes = 160 bits, per RFC 4226)
    secret_bytes = os.urandom(20)
    secret = base64.b32encode(secret_bytes).decode("utf-8").rstrip("=")

    # Store secret temporarily (not yet enabled — user must verify first)
    current_user.two_factor_secret = secret
    await db.commit()

    # Build otpauth URI
    issuer = "SocialAuto"
    label = f"{issuer}:{current_user.email}"
    qr_uri = f"otpauth://totp/{label}?secret={secret}&issuer={issuer}&algorithm=SHA1&digits=6&period=30"

    return TwoFactorSetupResponse(secret=secret, qr_uri=qr_uri)


@router.post("/2fa/verify")
async def verify_2fa(
    data: TwoFactorVerifyRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Verify a TOTP code and enable 2FA."""
    if not current_user.two_factor_secret:
        raise HTTPException(status_code=400, detail="2FA setup not initiated")

    if not _verify_totp(current_user.two_factor_secret, data.code):
        raise HTTPException(status_code=400, detail="Invalid verification code")

    current_user.two_factor_enabled = True
    await db.commit()

    return {"message": "Two-factor authentication enabled", "enabled": True}


@router.delete("/2fa")
async def disable_2fa(
    data: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Disable 2FA (requires password confirmation)."""
    from app.core.security import verify_password

    if not verify_password(data.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Password is incorrect")

    current_user.two_factor_enabled = False
    current_user.two_factor_secret = None
    await db.commit()

    return {"message": "Two-factor authentication disabled", "enabled": False}


def _verify_totp(secret: str, code: str, window: int = 1) -> bool:
    """Verify a TOTP code against the secret using a ±1 time window."""
    import base64
    import hashlib
    import hmac
    import struct
    import time

    # Decode the base32 secret
    padding = "=" * (8 - len(secret) % 8) if len(secret) % 8 else ""
    try:
        key = base64.b32decode(secret + padding)
    except Exception:
        return False

    # Check current time and ±window steps
    now = int(time.time())
    for offset in range(-window, window + 1):
        timestep = (now // 30) + offset
        msg = struct.pack(">Q", timestep)
        h = hmac.new(key, msg, hashlib.sha1).digest()
        offset_byte = h[-1] & 0x0F
        truncated = struct.unpack(">I", h[offset_byte:offset_byte + 4])[0] & 0x7FFFFFFF
        expected = str(truncated % 1000000).zfill(6)
        if hmac.compare_digest(expected, str(code).strip()):
            return True
    return False


# ── Notification preferences ──────────────────────────────────────────────────

class NotificationPreferencesRequest(BaseModel):
    email_new_post: bool = True
    email_scheduled: bool = True
    email_analytics: bool = False
    push_new_post: bool = True
    push_scheduled: bool = False


class NotificationPreferencesResponse(BaseModel):
    email_new_post: bool
    email_scheduled: bool
    email_analytics: bool
    push_new_post: bool
    push_scheduled: bool

    class Config:
        from_attributes = True


@router.get("/notifications/preferences", response_model=NotificationPreferencesResponse)
async def get_notification_preferences(
    current_user: User = Depends(get_current_user),
):
    """Get the user's notification preferences."""
    prefs = current_user.notification_preferences or {}
    return NotificationPreferencesResponse(
        email_new_post=prefs.get("email_new_post", True),
        email_scheduled=prefs.get("email_scheduled", True),
        email_analytics=prefs.get("email_analytics", False),
        push_new_post=prefs.get("push_new_post", True),
        push_scheduled=prefs.get("push_scheduled", False),
    )


@router.put("/notifications/preferences", response_model=NotificationPreferencesResponse)
async def update_notification_preferences(
    data: NotificationPreferencesRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update the user's notification preferences."""
    current_user.notification_preferences = data.model_dump()
    await db.commit()
    await db.refresh(current_user)
    return NotificationPreferencesResponse(**current_user.notification_preferences)


# ── Data export ───────────────────────────────────────────────────────────────

@router.get("/export-data")
async def export_user_data(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Export all user data (posts, media metadata, analytics) as JSON."""
    from app.models.analytics import PostAnalyticsSnapshot
    from app.models.content import MediaAsset, Post
    from app.models.social_account import SocialAccount

    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()
    if not team:
        return {"user": {"email": current_user.email}, "posts": [], "media": [], "accounts": [], "analytics": []}

    # Posts
    posts_result = await db.execute(
        select(Post).where(Post.team_id == team.id).order_by(Post.created_at.desc())
    )
    posts = posts_result.scalars().all()

    # Media assets
    media_result = await db.execute(
        select(MediaAsset).where(MediaAsset.team_id == team.id).order_by(MediaAsset.created_at.desc())
    )
    media = media_result.scalars().all()

    # Social accounts
    accounts_result = await db.execute(
        select(SocialAccount).where(SocialAccount.team_id == team.id)
    )
    accounts = accounts_result.scalars().all()

    # Analytics snapshots
    analytics_result = await db.execute(
        select(PostAnalyticsSnapshot).where(PostAnalyticsSnapshot.team_id == team.id).order_by(PostAnalyticsSnapshot.captured_at.desc()).limit(500)
    )
    snapshots = analytics_result.scalars().all()

    return {
        "user": {
            "email": current_user.email,
            "name": current_user.name,
            "timezone": current_user.timezone,
            "created_at": current_user.created_at.isoformat(),
        },
        "posts": [
            {
                "id": str(p.id),
                "content_text": p.content_text,
                "status": p.status.value if hasattr(p.status, "value") else str(p.status),
                "created_at": p.created_at.isoformat(),
                "scheduled_at": p.scheduled_at.isoformat() if p.scheduled_at else None,
            }
            for p in posts
        ],
        "media": [
            {
                "id": str(m.id),
                "filename": m.filename,
                "mime_type": m.mime_type,
                "size_bytes": m.size_bytes,
                "alt_text": m.alt_text,
                "tags": m.tags,
                "ai_tags": m.ai_tags,
                "ai_caption": m.ai_caption,
                "created_at": m.created_at.isoformat(),
            }
            for m in media
        ],
        "accounts": [
            {
                "platform": a.platform,
                "username": a.username,
                "display_name": a.display_name,
                "status": a.status,
            }
            for a in accounts
        ],
        "analytics": [
            {
                "platform": s.platform,
                "impressions": s.impressions,
                "clicks": s.clicks,
                "likes": s.likes,
                "comments": s.comments,
                "shares": s.shares,
                "engagement": s.engagement,
                "engagement_rate": s.engagement_rate,
                "captured_at": s.captured_at.isoformat(),
            }
            for s in snapshots
        ],
    }


# ── Delete account ────────────────────────────────────────────────────────────

class DeleteAccountRequest(BaseModel):
    password: str


@router.delete("/account", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    data: DeleteAccountRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Permanently delete the user's account and all associated data."""
    from app.core.security import verify_password

    if not verify_password(data.password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Password is incorrect")

    # The cascade="all, delete-orphan" on Team relationships will clean up
    # posts, media, accounts, workflows, etc. when the team is deleted.
    result = await db.execute(
        select(Team).where(Team.owner_id == current_user.id)
    )
    owned_teams = result.scalars().all()

    for team in owned_teams:
        await db.delete(team)

    # Remove team memberships
    result = await db.execute(
        select(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    memberships = result.scalars().all()
    for membership in memberships:
        await db.delete(membership)

    await db.delete(current_user)
    await db.commit()


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


@router.post("/forgot-password")
@limiter.limit("5/minute")
async def forgot_password(request: Request, data: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)):
    from app.core.security import create_reset_token
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()

    # Always return success to prevent email enumeration
    if not user:
        return {"message": "If the email exists, a password reset link has been sent"}

    reset_token = create_reset_token({"sub": str(user.id), "email": user.email})

    # Build the reset link from the configured frontend URL
    frontend_url = ""
    cors_origins = settings.CORS_ORIGINS
    for origin in cors_origins:
        if "8082" in origin or "3000" in origin or "3001" in origin:
            frontend_url = origin.rstrip("/")
            break
    if not frontend_url and cors_origins:
        frontend_url = cors_origins[0].rstrip("/")
    reset_link = f"{frontend_url}/reset-password?token={reset_token}"

    # Send password reset email via SMTP (Resend relay)
    try:
        from app.services.email_digest import send_email_smtp

        send_email_smtp(
            subject="Password Reset — SocialAuto",
            text_body=(
                f"You requested a password reset.\n\n"
                f"Click the link below to reset your password:\n{reset_link}\n\n"
                f"This link expires in 30 minutes.\n"
                f"If you did not request this, ignore this email."
            ),
            html_body=(
                f"<p>You requested a password reset.</p>"
                f"<p><a href=\"{reset_link}\">Reset your password</a></p>"
                f"<p>This link expires in 30 minutes.</p>"
                f"<p>If you did not request this, ignore this email.</p>"
            ),
            to_addrs=[user.email],
        )
    except Exception:
        logger.warning("Failed to send password reset email to %s", user.email)

    # In debug mode, return the token for testing
    if settings.DEBUG:
        return {"message": "If the email exists, a password reset link has been sent", "debug_token": reset_token}

    return {"message": "If the email exists, a password reset link has been sent"}


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


@router.post("/reset-password")
@limiter.limit("5/minute")
async def reset_password(request: Request, data: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    from app.core.security import decode_token, hash_password
    payload = decode_token(data.token)
    if not payload or payload.get("type") != "reset":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset token")

    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset token")

    user.password_hash = hash_password(data.new_password)
    await db.commit()

    return {"message": "Password has been reset successfully"}


# OAuth endpoints
@router.get("/oauth/{platform}/authorize")
async def oauth_authorize(platform: str, team_id: uuid.UUID, current_user: User = Depends(get_current_user)):
    if platform == "instagram2":
        # Instagram Business Login uses a custom flow
        return await instagram2_authorize(team_id, current_user)

    redirect_uri = getattr(settings, f"{platform.upper()}_REDIRECT_URI")
    client = globals()[f"{platform}_client"]

    # Fail fast: with an empty client_id the authorize URL would be built as
    # "…/authorize?client_id=&…", which the provider rejects (e.g. LinkedIn:
    # "You need to pass the 'client_id' parameter"). Never emit a broken URL.
    if not getattr(client, "client_id", None):
        env_var = _OAUTH_CLIENT_ID_ENV_VAR.get(platform, f"{platform.upper()}_CLIENT_ID")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"OAuth for '{platform}' is not configured: {env_var} is empty, "
                "so no client_id can be included in the authorization URL. "
                f"Set {env_var} in .env (or the Env Manager) and restart social-api, then retry."
            ),
        )
    if platform == "linkedin" and not getattr(client, "client_secret", None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "OAuth for 'linkedin' is not configured: LINKEDIN_CLIENT_SECRET is empty. "
                "Copy the Client Secret from the LinkedIn developer app Auth tab into .env, "
                "recreate social-api, then reconnect LinkedIn."
            ),
        )

    PLATFORM_SCOPES: dict[str, list[str]] = {
        "linkedin": LINKEDIN_SCOPES,
        "facebook": [
            "public_profile", "email",
            "pages_show_list", "pages_read_engagement", "pages_manage_posts",
        ],
        "instagram": [
            "instagram_basic", "instagram_content_publish",
            "pages_show_list", "pages_read_engagement", "pages_manage_posts",
        ],
        "instagram2": ["user_profile", "user_media"],
        "tiktok": ["user.info.basic", "video.publish", "video.upload"],
    }

    # TikTok requires client_key as the client_id param in the authorize URL
    extra_params: dict = {}
    if platform == "tiktok":
        extra_params["client_key"] = settings.TIKTOK_CLIENT_KEY

    authorization_url = await client.get_authorization_url(
        redirect_uri,
        state=str(team_id),
        scope=PLATFORM_SCOPES.get(platform),
        extras_params=extra_params,
    )

    return {"authorization_url": authorization_url}


def _platform_account_type(platform: str, account_id: str, token: dict, ctx: dict) -> tuple[str, bool]:
    """Determine account_type and is_business for a platform after OAuth.

    Returns (account_type, is_business):
      - LinkedIn person: ("person", False) — organizations handled by _sync_linkedin_organizations
      - Facebook page: ("page", True) — user timeline posting not supported by Graph API
      - Instagram business: ("business", True) — only business/creator accounts can publish
      - Twitter/Threads/TikTok: ("person", False) — no business distinction in posting API
    """
    if platform == "linkedin":
        return ("person", False)
    if platform == "facebook":
        # If account_id differs from the FB user id, it's a Page
        fb_user_id = (ctx.get("fb_info") or {}).get("id")
        if fb_user_id and account_id != fb_user_id:
            return ("page", True)
        return ("user", False)
    if platform == "instagram":
        # IG publishing requires a business/creator account
        return ("business", True)
    if platform == "tiktok":
        return ("person", False)
    # twitter, threads, and any future platform
    return ("person", False)


@router.get("/oauth/{platform}/callback")
async def oauth_callback(
    platform: str,
    state: str,
    db: AsyncSession = Depends(get_db),
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    if error:
        raise HTTPException(status_code=400, detail=f"OAuth error from {platform}: {error} — {error_description}")
    if not code:
        raise HTTPException(status_code=400, detail=f"No authorization code returned by {platform}")
    # Decode state — may be plain UUID or base64-JSON with PKCE verifier
    try:
        import base64 as _b64
        import json as _json
        _data = _json.loads(_b64.urlsafe_b64decode(state + "==").decode())
        team_id = uuid.UUID(_data["t"])
        code_verifier: str | None = _data.get("cv")
    except Exception:
        team_id = uuid.UUID(state)
        code_verifier = None

    redirect_uri = getattr(settings, f"{platform.upper()}_REDIRECT_URI")
    client = globals()[f"{platform}_client"]

    try:
        token = await client.get_access_token(code, redirect_uri, code_verifier=code_verifier)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Token exchange failed for {platform}: {exc}",
        ) from exc

    # Get user info from platform
    access_token = token["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient() as http:
        if platform == "linkedin":
            resp = await http.get("https://api.linkedin.com/v2/userinfo", headers=headers)
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "LinkedIn profile fetch failed "
                        f"(HTTP {resp.status_code}): {resp.text[:300]}"
                    ),
                )
            user_info = resp.json()
            account_id = user_info["sub"]
            username = user_info.get("email")
            display_name = f"{user_info.get('given_name', '')} {user_info.get('family_name', '')}".strip()
            avatar_url = user_info.get("picture")
            scopes = LINKEDIN_SCOPES
            # Discover company pages this member can post as (e.g. cloudless.gr).
            # Best-effort: a transient LinkedIn API / ACL failure must not fail the
            # whole connect after the user already granted consent on the OAuth page
            # (this is the #1 "connect just worked on LinkedIn but the app errors"
            # failure mode — re-connecting should still succeed and store the
            # personal account; /accounts/linkedin/sync-organizations can retry).
            try:
                await _sync_linkedin_organizations(
                    db=db,
                    team_id=team_id,
                    access_token=access_token,
                    refresh_token=token.get("refresh_token"),
                    scopes=scopes,
                    http=http,
                )
            except Exception:
                logger.exception(
                    "LinkedIn organization sync failed after consent; "
                    "continuing with personal account"
                )
        elif platform == "twitter":
            resp = await http.get("https://api.twitter.com/2/users/me", headers=headers)
            user_info = resp.json()
            account_id = user_info["data"]["id"]
            username = user_info["data"]["username"]
            display_name = user_info["data"]["name"]
            avatar_url = None
            scopes = ["tweet.read", "tweet.write", "users.read"]
        elif platform == "facebook":
            resp = await http.get(
                "https://graph.facebook.com/me",
                headers=headers,
                params={"fields": "id,name,email,picture"},
            )
            user_info = resp.json()
            if "error" in user_info:
                raise HTTPException(status_code=400, detail=f"Facebook /me failed: {user_info['error'].get('message')}")

            # Exchange short-lived user token for long-lived token (~60 days)
            ll_resp = await http.get(
                "https://graph.facebook.com/oauth/access_token",
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": settings.FACEBOOK_CLIENT_ID,
                    "client_secret": settings.FACEBOOK_CLIENT_SECRET,
                    "fb_exchange_token": access_token,
                },
            )
            ll_data = ll_resp.json()
            long_lived_token = ll_data.get("access_token", access_token)

            # Fetch managed pages — page tokens are permanent and required for posting
            pages_resp = await http.get(
                "https://graph.facebook.com/me/accounts",
                params={"fields": "id,name,access_token,picture", "access_token": long_lived_token},
            )
            pages = pages_resp.json().get("data", [])
            if pages:
                page = pages[0]
                account_id = page["id"]
                username = page.get("name")
                display_name = page.get("name", user_info.get("name", ""))
                avatar_url = (page.get("picture") or {}).get("data", {}).get("url") or (user_info.get("picture") or {}).get("data", {}).get("url")
                # Store the page token directly — it never expires and works for posting
                access_token = page.get("access_token", long_lived_token)
            else:
                account_id = user_info["id"]
                username = user_info.get("email") or user_info.get("name")
                display_name = user_info.get("name", "")
                avatar_url = (user_info.get("picture") or {}).get("data", {}).get("url")
                access_token = long_lived_token
            scopes = ["pages_show_list", "pages_read_engagement", "pages_manage_posts"]
        elif platform == "threads":
            resp = await http.get(
                "https://graph.threads.net/me",
                headers=headers,
                params={"fields": "id,username,name,threads_profile_picture_url"},
            )
            user_info = resp.json()
            account_id = user_info["id"]
            username = user_info.get("username")
            display_name = user_info.get("name") or username or ""
            avatar_url = user_info.get("threads_profile_picture_url")
            scopes = ["threads_basic", "threads_content_publish"]
        elif platform == "instagram":
            resp = await http.get("https://graph.facebook.com/me", headers=headers, params={"fields": "id,name,picture"})
            fb_info = resp.json()

            # Exchange for long-lived token (~60 days)
            ll_resp = await http.get(
                "https://graph.facebook.com/oauth/access_token",
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": settings.FACEBOOK_CLIENT_ID,
                    "client_secret": settings.FACEBOOK_CLIENT_SECRET,
                    "fb_exchange_token": access_token,
                },
            )
            long_lived_token = ll_resp.json().get("access_token", access_token)

            # Fetch all pages with page tokens + IG business account link
            ig_resp = await http.get(
                "https://graph.facebook.com/me/accounts",
                params={
                    "fields": "id,name,access_token,instagram_business_account{id,ig_id,username,profile_picture_url,name}",
                    "access_token": long_lived_token,
                },
            )
            ig_data = ig_resp.json()
            ig_account = None
            page_token = None

            for page in ig_data.get("data", []):
                if page.get("instagram_business_account"):
                    ig_account = page["instagram_business_account"]
                    page_token = page.get("access_token")
                    break

            # Fallback: check page_backed_instagram_accounts on each page
            if not ig_account:
                for page in ig_data.get("data", []):
                    pt = page.get("access_token", long_lived_token)
                    pbi_resp = await http.get(
                        f"https://graph.facebook.com/{page['id']}/page_backed_instagram_accounts",
                        params={"fields": "id,ig_id,username,profile_picture_url,name", "access_token": pt},
                    )
                    for acct in pbi_resp.json().get("data", []):
                        if acct.get("id"):
                            ig_account = acct
                            page_token = pt
                            break
                    if ig_account:
                        break

            # Fallback: check Business Manager owned pages
            if not ig_account:
                biz_resp = await http.get(
                    "https://graph.facebook.com/me/businesses",
                    params={"access_token": long_lived_token},
                )
                for biz in biz_resp.json().get("data", []):
                    biz_ig = await http.get(
                        f"https://graph.facebook.com/{biz['id']}/instagram_accounts",
                        params={"fields": "id,ig_id,username,profile_picture_url,name", "access_token": long_lived_token},
                    )
                    for acct in biz_ig.json().get("data", []):
                        if acct.get("id"):
                            ig_account = acct
                            page_token = long_lived_token
                            break
                    if ig_account:
                        break

            if ig_account:
                account_id = ig_account["id"]
                username = ig_account.get("username")
                display_name = ig_account.get("name") or ig_account.get("username", "")
                avatar_url = ig_account.get("profile_picture_url")
                access_token = page_token or long_lived_token
            else:
                account_id = fb_info["id"]
                username = fb_info.get("name")
                display_name = fb_info.get("name", "")
                avatar_url = fb_info.get("picture", {}).get("data", {}).get("url")
                access_token = long_lived_token
            scopes = ["instagram_basic", "instagram_content_publish", "pages_show_list"]
        elif platform == "tiktok":
            # TikTok token response includes open_id alongside access_token
            open_id = token.get("open_id", "")
            # Fetch basic user info
            resp = await http.get(
                "https://open.tiktokapis.com/v2/user/info/",
                params={"fields": "open_id,union_id,avatar_url,display_name"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            tt_info = resp.json().get("data", {}).get("user", {})
            account_id = open_id or tt_info.get("open_id", "")
            username = tt_info.get("display_name") or account_id
            display_name = tt_info.get("display_name", "")
            avatar_url = tt_info.get("avatar_url")
            # Store open_id in metadata — needed for every API call
            scopes = ["user.info.basic", "video.publish", "video.upload"]
        else:
            raise HTTPException(status_code=400, detail="Unsupported platform")

    # Save or update social account
    result = await db.execute(
        select(SocialAccount).where(
            SocialAccount.team_id == team_id,
            SocialAccount.platform == platform,
            SocialAccount.account_id == account_id,
        )
    )
    account = result.scalar_one_or_none()

    if account:
        account.access_token_enc = encrypt_token(access_token)
        if "refresh_token" in token:
            account.refresh_token_enc = encrypt_token(token["refresh_token"])
        expires_in = token.get("expires_in")
        if expires_in:
            from datetime import datetime, timedelta
            account.token_expires_at = datetime.now(UTC) + timedelta(seconds=int(expires_in))
        else:
            account.token_expires_at = None
        account.scopes = scopes
        account.status = "active"
        account.username = username
        account.display_name = display_name
        account.avatar_url = avatar_url
        # Set account_type and is_business per platform
        _acct_type, _is_biz = _platform_account_type(platform, account_id, token, locals())
        account.account_type = _acct_type
        account.is_business = _is_biz
        if platform == "linkedin":
            account.meta_data = {
                **(account.meta_data or {}),
                "account_type": "person",
                "author_urn": f"urn:li:person:{account_id}",
            }
        elif platform == "tiktok":
            account.meta_data = {
                **(account.meta_data or {}),
                "open_id": token.get("open_id", account_id),
            }
        elif platform == "facebook":
            account.meta_data = {
                **(account.meta_data or {}),
                "account_type": _acct_type,
                "page_id": account_id if _is_biz else None,
            }
        elif platform == "instagram":
            account.meta_data = {
                **(account.meta_data or {}),
                "account_type": _acct_type,
            }
    else:
        _meta: dict = {}
        if platform == "linkedin":
            _meta = {"account_type": "person", "author_urn": f"urn:li:person:{account_id}"}
        elif platform == "tiktok":
            _meta = {"open_id": token.get("open_id", account_id)}
        elif platform == "facebook":
            _meta = {"account_type": "page" if account_id != locals().get("fb_info", {}).get("id") else "user"}
        elif platform == "instagram":
            _meta = {"account_type": "business"}
        _expires_in = token.get("expires_in")
        _token_expires_at = None
        if _expires_in:
            from datetime import datetime, timedelta
            _token_expires_at = datetime.now(UTC) + timedelta(seconds=int(_expires_in))
        _acct_type, _is_biz = _platform_account_type(platform, account_id, token, locals())
        account = SocialAccount(
            team_id=team_id,
            platform=platform,
            account_id=account_id,
            username=username,
            display_name=display_name,
            avatar_url=avatar_url,
            access_token_enc=encrypt_token(access_token),
            refresh_token_enc=encrypt_token(token["refresh_token"]) if "refresh_token" in token else None,
            token_expires_at=_token_expires_at,
            scopes=scopes,
            status="active",
            account_type=_acct_type,
            is_business=_is_biz,
            meta_data=_meta,
        )
        db.add(account)

    await db.commit()

    return {"message": f"{platform} account connected successfully", "account_id": str(account.id)}


# ── Instagram Business Login (Instagram API with Instagram Login) ─────────────
# Separate path from the Facebook-Login flow above. Uses graph.instagram.com
# and an Instagram user token (no Facebook Page required).

@router.get("/oauth/instagram2/authorize")
async def instagram2_authorize(
    team_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
):
    """Initiate Instagram Business Login OAuth flow."""
    if not settings.INSTAGRAM2_CLIENT_ID:
        raise HTTPException(status_code=400, detail="Instagram Business Login not configured (INSTAGRAM2_CLIENT_ID missing)")

    state = json.dumps({"t": str(team_id)})
    state_b64 = base64.urlsafe_b64encode(state.encode()).rstrip(b"=").decode()

    scope = "user_profile,user_media"
    auth_url = (
        f"https://www.instagram.com/oauth/authorize"
        f"?client_id={settings.INSTAGRAM2_CLIENT_ID}"
        f"&redirect_uri={settings.INSTAGRAM2_REDIRECT_URI}"
        f"&scope={scope}"
        f"&response_type=code"
        f"&state={state_b64}"
    )
    return {"authorization_url": auth_url}


@router.get("/oauth/instagram2/callback")
async def instagram2_callback(
    state: str,
    db: AsyncSession = Depends(get_db),
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    """Handle Instagram Business Login callback."""
    if error:
        raise HTTPException(status_code=400, detail=f"OAuth error from Instagram: {error} — {error_description}")
    if not code:
        raise HTTPException(status_code=400, detail="No authorization code returned by Instagram")

    # Decode state
    try:
        state_data = json.loads(base64.urlsafe_b64decode(state + "==").decode())
        team_id = uuid.UUID(state_data["t"])
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid state parameter")

    async with httpx.AsyncClient(timeout=15.0) as http:
        # Exchange code for short-lived user token
        token_resp = await http.post(
            "https://api.instagram.com/oauth/access_token",
            data={
                "client_id": settings.INSTAGRAM2_CLIENT_ID,
                "client_secret": settings.INSTAGRAM2_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "redirect_uri": settings.INSTAGRAM2_REDIRECT_URI,
                "code": code,
            },
        )
        if token_resp.status_code != 200:
            raise HTTPException(status_code=400, detail=f"Instagram token exchange failed: {token_resp.text[:400]}")
        token_data = token_resp.json()

        ig_user_id = token_data.get("user_id")
        access_token = token_data.get("access_token")

        # Exchange for long-lived token (valid ~60 days, refreshable)
        ll_resp = await http.get(
            "https://graph.instagram.com/access_token",
            params={
                "grant_type": "ig_exchange_token",
                "client_secret": settings.INSTAGRAM2_CLIENT_SECRET,
                "access_token": access_token,
            },
        )
        ll_data = ll_resp.json()
        long_lived_token = ll_data.get("access_token", access_token)
        expires_in = ll_data.get("expires_in")

        # Get user profile info
        profile_resp = await http.get(
            f"https://graph.instagram.com/{ig_user_id}",
            params={"fields": "id,username,account_type,media_count", "access_token": long_lived_token},
        )
        profile = profile_resp.json() if profile_resp.status_code == 200 else {}

    # Store the account
    account_id = str(ig_user_id)
    username = profile.get("username", "")
    display_name = profile.get("username", "")
    scopes = ["user_profile", "user_media"]

    # Compute expiry
    token_expires_at = None
    if expires_in:
        from datetime import datetime, timedelta
        token_expires_at = datetime.now(UTC) + timedelta(seconds=int(expires_in))

    # Upsert
    result = await db.execute(
        select(SocialAccount).where(
            SocialAccount.team_id == team_id,
            SocialAccount.platform == "instagram",
            SocialAccount.account_id == account_id,
        )
    )
    account = result.scalar_one_or_none()

    ig_account_type = profile.get("account_type", "BUSINESS")
    is_biz = ig_account_type.upper() in ("BUSINESS", "CREATOR")

    if account:
        account.access_token_enc = encrypt_token(long_lived_token)
        account.token_expires_at = token_expires_at
        account.scopes = scopes
        account.status = "active"
        account.username = username
        account.display_name = display_name
        account.account_type = ig_account_type.lower()
        account.is_business = is_biz
        account.meta_data = {
            **(account.meta_data or {}),
            "account_type": ig_account_type,
            "ig_business_id": account_id,
            "login_type": "business_login",
        }
    else:
        account = SocialAccount(
            team_id=team_id,
            platform="instagram",
            account_id=account_id,
            username=username,
            display_name=display_name,
            access_token_enc=encrypt_token(long_lived_token),
            token_expires_at=token_expires_at,
            scopes=scopes,
            status="active",
            account_type=ig_account_type.lower(),
            is_business=is_biz,
            meta_data={
                "account_type": ig_account_type,
                "ig_business_id": account_id,
                "login_type": "business_login",
            },
        )
        db.add(account)

    await db.commit()

    return {"message": "instagram account connected successfully", "account_id": str(account.id)}


@router.post("/linkedin/sync-orgs")
async def linkedin_sync_orgs(
    db: AsyncSession = Depends(get_db),
    current_user: "User" = Depends(get_current_user),
):
    """Re-sync LinkedIn organization pages for the current user's team."""
    from app.core.security import decrypt_token

    result = await db.execute(
        select(SocialAccount).where(
            SocialAccount.platform == "linkedin",
        )
    )
    accounts = result.scalars().all()
    personal = next(
        (a for a in accounts if (a.meta_data or {}).get("account_type") == "person"),
        accounts[0] if accounts else None,
    )
    if not personal:
        raise HTTPException(status_code=404, detail="No LinkedIn account connected")

    try:
        raw_enc = bytes(personal.access_token_enc)
        access_token = decrypt_token(raw_enc)
        refresh_raw = bytes(personal.refresh_token_enc) if personal.refresh_token_enc else None
        refresh_token = decrypt_token(refresh_raw) if refresh_raw else None
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Token decrypt failed: {exc}")

    # Probe what scopes LinkedIn actually accepted
    async with httpx.AsyncClient(timeout=15.0) as http:
        probe = await http.get(
            "https://api.linkedin.com/rest/organizationAcls?q=roleAssignee&role=ADMINISTRATOR&state=APPROVED",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Linkedin-Version": "202608",
                "X-Restli-Protocol-Version": "2.0.0",
            },
        )
        raw = probe.json()

    if probe.status_code != 200:
        raise HTTPException(
            status_code=probe.status_code,
            detail=f"LinkedIn org ACL query failed ({probe.status_code}): {raw}",
        )

    async with httpx.AsyncClient(timeout=30.0) as http:
        synced = await _sync_linkedin_organizations(
            db=db,
            team_id=personal.team_id,
            access_token=access_token,
            refresh_token=refresh_token,
            scopes=personal.scopes or [],
            http=http,
        )
    await db.commit()

    return {
        "synced": len(synced),
        "organizations": [{"id": str(a.id), "display_name": a.display_name, "account_id": a.account_id} for a in synced],
        "raw_acl_response": raw,
    }
