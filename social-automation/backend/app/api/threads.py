"""Threads API router — profile, insights, publishing, reply management.

Exposes Threads-specific endpoints:
- Profile read (Graph API + browser bridge fallback)
- Profile update (bio/name via browser bridge)
- Account insights (views, likes, replies, reposts, quotes, followers)
- Post insights (per-post metrics)
- Publishing quota (250 posts / 24h)
- Reply to a thread
- Delete a post
- List published posts
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.core.security import decrypt_token
from app.db.session import get_db
from app.models.social_account import SocialAccount
from app.models.user import Team, TeamMember, User
from app.services.threads_api import ThreadsAPIClient, ThreadsAPIError

router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _get_team(db: AsyncSession, user: User) -> Team | None:
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == user.id)
    )
    return result.scalars().first()


async def _get_threads_account(
    db: AsyncSession,
    team: Team,
    account_id: uuid.UUID,
) -> SocialAccount:
    """Resolve a Threads social account for the team."""
    acct = (
        await db.execute(
            select(SocialAccount).where(
                SocialAccount.id == account_id,
                SocialAccount.team_id == team.id,
                SocialAccount.platform == "threads",
            )
        )
    ).scalar_one_or_none()
    if not acct:
        raise HTTPException(status_code=404, detail="Threads account not found")
    return acct


async def _get_threads_client(
    db: AsyncSession,
    team: Team,
    account_id: uuid.UUID,
) -> tuple[SocialAccount, ThreadsAPIClient]:
    """Resolve a Threads account and return (account, authenticated client)."""
    acct = await _get_threads_account(db, team, account_id)
    if not acct.access_token_enc:
        raise HTTPException(status_code=400, detail="Threads account has no access token")
    token = decrypt_token(acct.access_token_enc)
    if not acct.account_id:
        raise HTTPException(status_code=400, detail="Threads account has no user ID")
    client = ThreadsAPIClient(access_token=token, user_id=acct.account_id)
    return acct, client


def _get_browser_bridge_client():
    """Lazy import and construct the browser bridge client."""
    from app.services.browser_bridge import BrowserBridgeClient, BrowserBridgeError

    import os
    bridge_url = os.getenv("BROWSER_BRIDGE_URL", "http://localhost:9223")
    return BrowserBridgeClient(bridge_url)


# ── Response models ───────────────────────────────────────────────────────────


class ThreadsProfileResponse(BaseModel):
    id: str | None = None
    username: str | None = None
    name: str | None = None
    threads_profile_picture_url: str | None = None
    threads_biography: str | None = None
    is_verified: bool = False
    followers_count: int | None = None


class ThreadsProfileUpdateRequest(BaseModel):
    biography: str | None = None
    full_name: str | None = None
    website: str | None = None


class ThreadsProfileUpdateResponse(BaseModel):
    success: bool
    updated_fields: list[str] = []
    ignored_fields: list[str] = []
    message: str = ""


class ThreadsInsightsResponse(BaseModel):
    metric: str
    values: list[dict[str, Any]] = []


class ThreadsPostInsightsResponse(BaseModel):
    media_id: str
    metrics: dict[str, Any] = {}


class ThreadsQuotaResponse(BaseModel):
    remaining: int
    total: int = 250
    used: int = 0


class ThreadsReplyRequest(BaseModel):
    text: str


class ThreadsReplyResponse(BaseModel):
    media_id: str
    success: bool = True


class ThreadsDeleteResponse(BaseModel):
    success: bool
    media_id: str


class ThreadsPostOut(BaseModel):
    id: str
    text: str | None = None
    timestamp: str | None = None
    media_type: str | None = None
    permalink: str | None = None


class ThreadsPostListResponse(BaseModel):
    posts: list[ThreadsPostOut] = []
    paging: dict[str, Any] | None = None


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/profile", response_model=ThreadsProfileResponse)
async def get_threads_profile(
    account_id: uuid.UUID = Query(..., description="Threads social account ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Read the Threads profile via the Graph API."""
    team = await _get_team(db, current_user)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    _, client = await _get_threads_client(db, team, account_id)
    try:
        data = await client.get_profile()
    except ThreadsAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.response_text)
    return ThreadsProfileResponse(
        id=data.get("id"),
        username=data.get("username"),
        name=data.get("name"),
        threads_profile_picture_url=data.get("threads_profile_picture_url"),
        threads_biography=data.get("threads_biography"),
        is_verified=data.get("is_verified", False),
    )


@router.put("/profile", response_model=ThreadsProfileUpdateResponse)
async def update_threads_profile(
    request: ThreadsProfileUpdateRequest,
    account_id: uuid.UUID = Query(..., description="Threads social account ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update the Threads profile (bio/name) via the browser bridge.

    Threads has no official write API for profile fields, so this endpoint
    automates the web UI through the logged-in browser session.
    """
    team = await _get_team(db, current_user)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    acct = await _get_threads_account(db, team, account_id)
    if not acct.username:
        raise HTTPException(status_code=400, detail="Threads account has no username")

    from app.services.browser_bridge import BrowserBridgeError

    bridge = _get_browser_bridge_client()
    try:
        result = await bridge.update_threads_profile(
            acct.username,
            biography=request.biography,
            full_name=request.full_name,
            website=request.website,
        )
    except BrowserBridgeError as e:
        raise HTTPException(status_code=503, detail=f"Threads profile update failed: {e.detail}")

    # Sync bio back to DB metadata
    if request.biography:
        acct.meta_data = {**(acct.meta_data or {}), "biography": request.biography}
        await db.commit()

    return ThreadsProfileUpdateResponse(
        success=result.get("status") == "updated",
        updated_fields=result.get("updated_fields", []),
        ignored_fields=result.get("ignored_fields", []),
        message="Threads profile updated via browser bridge",
    )


@router.get("/insights", response_model=ThreadsInsightsResponse)
async def get_threads_insights(
    account_id: uuid.UUID = Query(..., description="Threads social account ID"),
    metric: str = Query("views", description="Insight metric: views, likes, replies, reposts, quotes, followers_count"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch account-level insights for a Threads account."""
    team = await _get_team(db, current_user)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    _, client = await _get_threads_client(db, team, account_id)
    try:
        data = await client.get_insights(metric=metric)
    except ThreadsAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.response_text)
    return ThreadsInsightsResponse(
        metric=metric,
        values=data.get("data", []),
    )


@router.get("/posts/{media_id}/insights", response_model=ThreadsPostInsightsResponse)
async def get_threads_post_insights(
    media_id: str,
    account_id: uuid.UUID = Query(..., description="Threads social account ID"),
    metric: str = Query("views", description="Insight metric: views, likes, replies, reposts, quotes"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch insights for a specific Threads post."""
    team = await _get_team(db, current_user)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    _, client = await _get_threads_client(db, team, account_id)
    import httpx
    url = f"https://graph.threads.net/v1.0/{media_id}/insights"
    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.get(
            url,
            headers={"Authorization": f"Bearer {client.access_token}"},
            params={"metric": metric},
        )
        if resp.status_code >= 400:
            raise HTTPException(
                status_code=resp.status_code,
                detail=f"Threads post insights error: {resp.text[:300]}",
            )
        data = resp.json()
    return ThreadsPostInsightsResponse(
        media_id=media_id,
        metrics=data.get("data", {}),
    )


@router.get("/quota", response_model=ThreadsQuotaResponse)
async def get_threads_quota(
    account_id: uuid.UUID = Query(..., description="Threads social account ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Check the Threads publishing quota (250 posts per 24 hours)."""
    team = await _get_team(db, current_user)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    _, client = await _get_threads_client(db, team, account_id)

    import httpx
    url = f"https://graph.threads.net/v1.0/{client.user_id}/threads_publishing_limit"
    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.get(
            url,
            headers={"Authorization": f"Bearer {client.access_token}"},
            params={"fields": "quota_usage"},
        )
        if resp.status_code >= 400:
            # If the quota endpoint fails, return defaults
            return ThreadsQuotaResponse(remaining=250, total=250, used=0)
        data = resp.json()

    quota_data = (data.get("data") or [{}])[0]
    used = 0
    for item in (quota_data.get("quota_usage") or []):
        if item.get("metric") == "publish_count":
            used = int(item.get("value", 0))
            break
    config = quota_data.get("config") or {}
    total = int(config.get("quota_total", 250))
    return ThreadsQuotaResponse(remaining=max(0, total - used), total=total, used=used)


@router.get("/posts", response_model=ThreadsPostListResponse)
async def list_threads_posts(
    account_id: uuid.UUID = Query(..., description="Threads social account ID"),
    limit: int = Query(25, ge=1, le=100),
    after: str | None = Query(None, description="Cursor for pagination"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List published Threads posts for the authenticated user."""
    team = await _get_team(db, current_user)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    _, client = await _get_threads_client(db, team, account_id)

    import httpx
    url = f"https://graph.threads.net/v1.0/{client.user_id}/threads"
    params: dict[str, Any] = {
        "fields": "id,text,timestamp,media_type,permalink",
        "limit": limit,
    }
    if after:
        params["after"] = after
    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.get(
            url,
            headers={"Authorization": f"Bearer {client.access_token}"},
            params=params,
        )
        if resp.status_code >= 400:
            raise HTTPException(
                status_code=resp.status_code,
                detail=f"Threads list posts error: {resp.text[:300]}",
            )
        data = resp.json()

    posts = [
        ThreadsPostOut(
            id=str(p.get("id", "")),
            text=p.get("text"),
            timestamp=p.get("timestamp"),
            media_type=p.get("media_type"),
            permalink=p.get("permalink"),
        )
        for p in data.get("data", [])
    ]
    return ThreadsPostListResponse(
        posts=posts,
        paging=data.get("paging"),
    )


@router.post("/posts/{media_id}/reply", response_model=ThreadsReplyResponse)
async def reply_to_thread(
    media_id: str,
    request: ThreadsReplyRequest,
    account_id: uuid.UUID = Query(..., description="Threads social account ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Reply to an existing Threads post.

    Creates a text container as a reply and publishes it.
    Requires the ``threads_manage_replies`` permission.
    """
    team = await _get_team(db, current_user)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    _, client = await _get_threads_client(db, team, account_id)

    if not request.text or not request.text.strip():
        raise HTTPException(status_code=400, detail="Reply text is required")

    import httpx

    # Step 1: Create a reply container
    create_url = f"https://graph.threads.net/v1.0/{client.user_id}/threads"
    create_payload: dict[str, Any] = {
        "media_type": "TEXT",
        "text": request.text[:500],
        "reply_to_id": media_id,
        "access_token": client.access_token,
    }
    async with httpx.AsyncClient(timeout=60.0) as http:
        resp = await http.post(create_url, data=create_payload)
        if resp.status_code >= 400:
            raise HTTPException(
                status_code=resp.status_code,
                detail=f"Threads reply creation failed: {resp.text[:300]}",
            )
        creation_id = (resp.json() or {}).get("id")
        if not creation_id:
            raise HTTPException(status_code=500, detail="Threads reply creation returned no ID")

    # Step 2: Publish the reply container
    publish_url = f"https://graph.threads.net/v1.0/{client.user_id}/threads_publish"
    publish_payload = {
        "creation_id": creation_id,
        "access_token": client.access_token,
    }
    async with httpx.AsyncClient(timeout=60.0) as http:
        resp = await http.post(publish_url, data=publish_payload)
        if resp.status_code >= 400:
            raise HTTPException(
                status_code=resp.status_code,
                detail=f"Threads reply publish failed: {resp.text[:300]}",
            )
        published_id = (resp.json() or {}).get("id", "")

    return ThreadsReplyResponse(media_id=str(published_id))


@router.delete("/posts/{media_id}", response_model=ThreadsDeleteResponse)
async def delete_threads_post(
    media_id: str,
    account_id: uuid.UUID = Query(..., description="Threads social account ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a published Threads post."""
    team = await _get_team(db, current_user)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    _, client = await _get_threads_client(db, team, account_id)
    try:
        success = await client.delete_post(media_id)
    except ThreadsAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.response_text)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to delete Threads post")
    return ThreadsDeleteResponse(success=True, media_id=media_id)


@router.get("/followers", response_model=ThreadsInsightsResponse)
async def get_threads_followers(
    account_id: uuid.UUID = Query(..., description="Threads social account ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch the Threads follower count via the insights endpoint."""
    team = await _get_team(db, current_user)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    _, client = await _get_threads_client(db, team, account_id)

    import httpx
    url = f"https://graph.threads.net/v1.0/{client.user_id}/insights"
    async with httpx.AsyncClient(timeout=15.0) as http:
        resp = await http.get(
            url,
            headers={"Authorization": f"Bearer {client.access_token}"},
            params={"metric": "followers_count"},
        )
        if resp.status_code >= 400:
            raise HTTPException(
                status_code=resp.status_code,
                detail=f"Threads followers error: {resp.text[:300]}",
            )
        data = resp.json()
    return ThreadsInsightsResponse(
        metric="followers_count",
        values=data.get("data", []),
    )
