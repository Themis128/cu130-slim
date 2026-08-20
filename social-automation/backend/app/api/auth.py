from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, EmailStr
from httpx_oauth.clients.linkedin import LinkedInOAuth2
from httpx_oauth.clients.facebook import FacebookOAuth2
from httpx_oauth.oauth2 import BaseOAuth2
from app.core.config import get_settings
from app.core.security import (
    verify_password,
    hash_password,
    create_access_token,
    create_refresh_token,
    decode_token,
    encrypt_token,
    decrypt_token,
)
from app.db.session import get_db
from app.models.user import User, Team, TeamMember, UserRole
from app.models.social_account import SocialAccount
import uuid

settings = get_settings()

router = APIRouter()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_PREFIX}/auth/login")

linkedin_client = LinkedInOAuth2(settings.LINKEDIN_CLIENT_ID, settings.LINKEDIN_CLIENT_SECRET)
# Twitter OAuth2 (using BaseOAuth2)
twitter_client = BaseOAuth2(
    settings.TWITTER_CLIENT_ID,
    settings.TWITTER_CLIENT_SECRET,
    authorize_endpoint="https://twitter.com/i/oauth2/authorize",
    access_token_endpoint="https://api.twitter.com/2/oauth2/token",
    refresh_token_endpoint="https://api.twitter.com/2/oauth2/token",
    base_scopes=["tweet.read", "tweet.write", "users.read", "offline.access"],
    name="twitter",
)
facebook_client = FacebookOAuth2(settings.FACEBOOK_CLIENT_ID, settings.FACEBOOK_CLIENT_SECRET)
# Instagram OAuth2 (using BaseOAuth2)
instagram_client = BaseOAuth2(
    settings.INSTAGRAM_CLIENT_ID,
    settings.INSTAGRAM_CLIENT_SECRET,
    authorize_endpoint="https://api.instagram.com/oauth/authorize",
    access_token_endpoint="https://api.instagram.com/oauth/access_token",
    refresh_token_endpoint="https://api.instagram.com/oauth/access_token",
    base_scopes=["instagram_basic", "instagram_content_publish"],
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


# OAuth endpoints
@router.get("/oauth/{platform}/authorize")
async def oauth_authorize(platform: str, team_id: uuid.UUID, current_user: User = Depends(get_current_user)):
    redirect_uri = getattr(settings, f"{platform.upper()}_REDIRECT_URI")
    client = getattr(globals(), f"{platform}_client")

    authorization_url = await client.get_authorization_url(
        redirect_uri,
        state=str(team_id),
        scope=["r_liteprofile", "r_emailaddress", "w_member_social"] if platform == "linkedin" else None,
    )

    return {"authorization_url": authorization_url}


@router.get("/oauth/{platform}/callback")
async def oauth_callback(
    platform: str,
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db),
):
    team_id = uuid.UUID(state)
    redirect_uri = getattr(settings, f"{platform.upper()}_REDIRECT_URI")
    client = getattr(globals(), f"{platform}_client")

    token = await client.get_access_token(code, redirect_uri)

    # Get user info from platform
    if platform == "linkedin":
        async with client.get_async_session(token["access_token"]) as session:
            resp = await session.get("https://api.linkedin.com/v2/userinfo")
            user_info = resp.json()
            account_id = user_info["sub"]
            username = user_info.get("email")
            display_name = f"{user_info.get('given_name', '')} {user_info.get('family_name', '')}".strip()
            avatar_url = user_info.get("picture")
            scopes = ["r_liteprofile", "r_emailaddress", "w_member_social"]
    elif platform == "twitter":
        async with client.get_async_session(token["access_token"]) as session:
            resp = await session.get("https://api.twitter.com/2/users/me")
            user_info = resp.json()
            account_id = user_info["data"]["id"]
            username = user_info["data"]["username"]
            display_name = user_info["data"]["name"]
            avatar_url = None
            scopes = ["tweet.read", "tweet.write", "users.read"]
    elif platform == "facebook":
        async with client.get_async_session(token["access_token"]) as session:
            resp = await session.get("https://graph.facebook.com/me", params={"fields": "id,name,email,picture"})
            user_info = resp.json()
            account_id = user_info["id"]
            username = user_info.get("email")
            display_name = user_info["name"]
            avatar_url = user_info.get("picture", {}).get("data", {}).get("url")
            scopes = ["pages_show_list", "pages_read_engagement", "pages_manage_posts"]
    elif platform == "instagram":
        async with client.get_async_session(token["access_token"]) as session:
            resp = await session.get("https://graph.facebook.com/me", params={"fields": "id,username,account_type,media_count"})
            user_info = resp.json()
            account_id = user_info["id"]
            username = user_info["username"]
            display_name = user_info["username"]
            avatar_url = None
            scopes = ["instagram_basic", "instagram_content_publish"]
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
        )
        db.add(account)

    await db.commit()

    return {"message": f"{platform} account connected successfully", "account_id": str(account.id)}