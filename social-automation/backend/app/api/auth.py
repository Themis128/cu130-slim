import uuid

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

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_PREFIX}/auth/login")

linkedin_client = LinkedInOAuth2(settings.LINKEDIN_CLIENT_ID, settings.LINKEDIN_CLIENT_SECRET)


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
async def register(user_data: UserCreate, db: AsyncSession = Depends(get_db)):
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
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
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


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


@router.post("/forgot-password")
async def forgot_password(data: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)):
    from app.core.security import create_reset_token
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()

    # Always return success to prevent email enumeration
    if not user:
        return {"message": "If the email exists, a password reset link has been sent"}

    reset_token = create_reset_token({"sub": str(user.id), "email": user.email})

    # TODO: Send email with reset link
    # For now, log the token (in production, send via email service)
    print(f"Password reset token for {user.email}: {reset_token}")

    # In debug mode, return the token for testing
    if settings.DEBUG:
        return {"message": "If the email exists, a password reset link has been sent", "debug_token": reset_token}

    return {"message": "If the email exists, a password reset link has been sent"}


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


@router.post("/reset-password")
async def reset_password(data: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
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
    redirect_uri = getattr(settings, f"{platform.upper()}_REDIRECT_URI")
    client = globals()[f"{platform}_client"]

    authorization_url = await client.get_authorization_url(
        redirect_uri,
        state=str(team_id),
        scope=["openid", "profile", "email", "w_member_social", "w_organization_social", "r_organization_social", "r_organization_admin"] if platform == "linkedin" else None,
    )

    return {"authorization_url": authorization_url}


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

    token = await client.get_access_token(code, redirect_uri, code_verifier=code_verifier)

    # Get user info from platform
    access_token = token["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient() as http:
        if platform == "linkedin":
            resp = await http.get("https://api.linkedin.com/v2/userinfo", headers=headers)
            user_info = resp.json()
            account_id = user_info["sub"]
            username = user_info.get("email")
            display_name = f"{user_info.get('given_name', '')} {user_info.get('family_name', '')}".strip()
            avatar_url = user_info.get("picture")
            scopes = [
                "openid",
                "profile",
                "email",
                "w_member_social",
                "w_organization_social",
                "r_organization_social",
                "r_organization_admin",
            ]
            # Discover company pages this member can post as (e.g. cloudless.gr)
            await _sync_linkedin_organizations(
                db=db,
                team_id=team_id,
                access_token=access_token,
                refresh_token=token.get("refresh_token"),
                scopes=scopes,
                http=http,
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
            resp = await http.get("https://graph.facebook.com/me", headers=headers, params={"fields": "id,name,email,picture"})
            user_info = resp.json()
            account_id = user_info["id"]
            username = user_info.get("email")
            display_name = user_info["name"]
            avatar_url = user_info.get("picture", {}).get("data", {}).get("url")
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
            # Get Facebook user, then find linked Instagram business account
            resp = await http.get("https://graph.facebook.com/me", headers=headers, params={"fields": "id,name,picture"})
            fb_info = resp.json()
            # Try to get linked Instagram account via pages
            ig_resp = await http.get(
                "https://graph.facebook.com/me/accounts",
                headers=headers,
                params={"fields": "instagram_business_account{id,username,profile_picture_url,name}"}
            )
            ig_data = ig_resp.json()
            ig_account = None
            for page in ig_data.get("data", []):
                if page.get("instagram_business_account"):
                    ig_account = page["instagram_business_account"]
                    break
            if ig_account:
                account_id = ig_account["id"]
                username = ig_account.get("username")
                display_name = ig_account.get("name") or ig_account.get("username", "")
                avatar_url = ig_account.get("profile_picture_url")
            else:
                account_id = fb_info["id"]
                username = fb_info.get("name")
                display_name = fb_info.get("name", "")
                avatar_url = fb_info.get("picture", {}).get("data", {}).get("url")
            scopes = ["instagram_basic", "instagram_content_publish", "pages_show_list"]
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
        account.access_token_enc = encrypt_token(token["access_token"])
        if "refresh_token" in token:
            account.refresh_token_enc = encrypt_token(token["refresh_token"])
        account.token_expires_at = None  # TODO: parse expires_in
        account.scopes = scopes
        account.status = "active"
        account.username = username
        account.display_name = display_name
        account.avatar_url = avatar_url
        if platform == "linkedin":
            account.meta_data = {
                **(account.meta_data or {}),
                "account_type": "person",
                "author_urn": f"urn:li:person:{account_id}",
            }
    else:
        account = SocialAccount(
            team_id=team_id,
            platform=platform,
            account_id=account_id,
            username=username,
            display_name=display_name,
            avatar_url=avatar_url,
            access_token_enc=encrypt_token(token["access_token"]),
            refresh_token_enc=encrypt_token(token["refresh_token"]) if "refresh_token" in token else None,
            scopes=scopes,
            status="active",
            meta_data=(
                {"account_type": "person", "author_urn": f"urn:li:person:{account_id}"}
                if platform == "linkedin"
                else {}
            ),
        )
        db.add(account)

    await db.commit()

    return {"message": f"{platform} account connected successfully", "account_id": str(account.id)}
