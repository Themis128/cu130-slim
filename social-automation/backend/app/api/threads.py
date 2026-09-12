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

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TeamId
from app.core.security import decrypt_token
from app.db.session import get_db
from app.models.social_account import SocialAccount
from app.services.threads_api import ThreadsAPIClient, ThreadsAPIError

router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _get_threads_account(
    db: AsyncSession,
    team_id: uuid.UUID,
    account_id: uuid.UUID,
) -> SocialAccount:
    """Resolve a Threads social account for the team."""
    acct = (
        await db.execute(
            select(SocialAccount).where(
                SocialAccount.id == account_id,
                SocialAccount.team_id == team_id,
                SocialAccount.platform == "threads",
            )
        )
    ).scalar_one_or_none()
    if not acct:
        raise HTTPException(status_code=404, detail="Threads account not found")
    return acct


async def _get_threads_client(
    db: AsyncSession,
    team_id: uuid.UUID,
    account_id: uuid.UUID,
) -> tuple[SocialAccount, ThreadsAPIClient]:
    """Resolve a Threads account and return (account, authenticated client)."""
    acct = await _get_threads_account(db, team_id, account_id)
    if not acct.access_token_enc:
        raise HTTPException(status_code=400, detail="Threads account has no access token")
    token = decrypt_token(acct.access_token_enc)
    if not acct.account_id:
        raise HTTPException(status_code=400, detail="Threads account has no user ID")
    client = ThreadsAPIClient(access_token=token, user_id=acct.account_id)
    return acct, client


def _get_browser_bridge_client():
    """Lazy import and construct the browser bridge client."""
    import os

    from app.services.browser_bridge import BrowserBridgeClient

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
    team_id: TeamId,
    account_id: uuid.UUID = Query(..., description="Threads social account ID"),
    db: AsyncSession = Depends(get_db),
):
    """Read the Threads profile via the Graph API."""
    _, client = await _get_threads_client(db, team_id, account_id)
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
    team_id: TeamId,
    account_id: uuid.UUID = Query(..., description="Threads social account ID"),
    db: AsyncSession = Depends(get_db),
):
    """Update the Threads profile (bio/name) via the browser bridge.

    Threads has no official write API for profile fields, so this endpoint
    automates the web UI through the logged-in browser session.
    """
    acct = await _get_threads_account(db, team_id, account_id)
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
    team_id: TeamId,
    account_id: uuid.UUID = Query(..., description="Threads social account ID"),
    metric: str = Query("views", description="Insight metric: views, likes, replies, reposts, quotes, followers_count"),
    db: AsyncSession = Depends(get_db),
):
    """Fetch account-level insights for a Threads account.

    The Threads insights API requires app review for production use.
    In development mode, this may return empty values.
    """
    _, client = await _get_threads_client(db, team_id, account_id)
    try:
        data = await client.get_insights(metric=metric)
    except ThreadsAPIError:
        return ThreadsInsightsResponse(metric=metric, values=[])
    return ThreadsInsightsResponse(
        metric=metric,
        values=data.get("data", []),
    )


@router.get("/posts/{media_id}/insights", response_model=ThreadsPostInsightsResponse)
async def get_threads_post_insights(
    media_id: str,
    team_id: TeamId,
    account_id: uuid.UUID = Query(..., description="Threads social account ID"),
    metric: str = Query("views", description="Insight metric: views, likes, replies, reposts, quotes"),
    db: AsyncSession = Depends(get_db),
):
    """Fetch insights for a specific Threads post."""
    _, client = await _get_threads_client(db, team_id, account_id)
    import httpx

    from app.services.threads_api import _validate_media_id

    try:
        safe_media_id = _validate_media_id(media_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    url = f"https://graph.threads.net/v1.0/{safe_media_id}/insights"
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
    team_id: TeamId,
    account_id: uuid.UUID = Query(..., description="Threads social account ID"),
    db: AsyncSession = Depends(get_db),
):
    """Check the Threads publishing quota (250 posts per 24 hours)."""
    _, client = await _get_threads_client(db, team_id, account_id)

    import httpx
    url = f"https://graph.threads.net/v1.0/{client.user_id}/threads_publishing_limit"
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.get(
                url,
                headers={"Authorization": f"Bearer {client.access_token}"},
                params={"fields": "quota_usage"},
            )
        if resp.status_code >= 400:
            return ThreadsQuotaResponse(remaining=250, total=250, used=0)
        data = resp.json()
    except Exception:
        return ThreadsQuotaResponse(remaining=250, total=250, used=0)

    quota_data = (data.get("data") or [{}])[0]
    used = 0
    quota_usage = quota_data.get("quota_usage") or []
    if isinstance(quota_usage, list):
        for item in quota_usage:
            if isinstance(item, dict) and item.get("metric") == "publish_count":
                used = int(item.get("value", 0))
                break
    config = quota_data.get("config") or {}
    total = int(config.get("quota_total", 250)) if isinstance(config, dict) else 250
    return ThreadsQuotaResponse(remaining=max(0, total - used), total=total, used=used)


@router.get("/posts", response_model=ThreadsPostListResponse)
async def list_threads_posts(
    team_id: TeamId,
    account_id: uuid.UUID = Query(..., description="Threads social account ID"),
    limit: int = Query(25, ge=1, le=100),
    after: str | None = Query(None, description="Cursor for pagination"),
    db: AsyncSession = Depends(get_db),
):
    """List published Threads posts for the authenticated user."""
    _, client = await _get_threads_client(db, team_id, account_id)

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
    team_id: TeamId,
    request: ThreadsReplyRequest,
    account_id: uuid.UUID = Query(..., description="Threads social account ID"),
    db: AsyncSession = Depends(get_db),
):
    """Reply to an existing Threads post.

    Creates a text container as a reply and publishes it.
    Requires the ``threads_manage_replies`` permission.
    """
    _, client = await _get_threads_client(db, team_id, account_id)

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
    team_id: TeamId,
    account_id: uuid.UUID = Query(..., description="Threads social account ID"),
    db: AsyncSession = Depends(get_db),
):
    """Delete a published Threads post."""
    _, client = await _get_threads_client(db, team_id, account_id)
    try:
        success = await client.delete_post(media_id)
    except ThreadsAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.response_text)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to delete Threads post")
    return ThreadsDeleteResponse(success=True, media_id=media_id)


@router.get("/followers", response_model=ThreadsInsightsResponse)
async def get_threads_followers(
    team_id: TeamId,
    account_id: uuid.UUID = Query(..., description="Threads social account ID"),
    db: AsyncSession = Depends(get_db),
):
    """Fetch the Threads follower count via the insights endpoint.

    The Threads insights API requires app review for production use.
    In development mode, this may return an error — we return empty
    values instead of failing.
    """
    _, client = await _get_threads_client(db, team_id, account_id)

    import httpx
    url = f"https://graph.threads.net/v1.0/{client.user_id}/insights"
    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            resp = await http.get(
                url,
                headers={"Authorization": f"Bearer {client.access_token}"},
                params={"metric": "followers_count"},
            )
        if resp.status_code >= 400:
            return ThreadsInsightsResponse(metric="followers_count", values=[])
        data = resp.json()
    except Exception:
        return ThreadsInsightsResponse(metric="followers_count", values=[])
    return ThreadsInsightsResponse(
        metric="followers_count",
        values=data.get("data", []),
    )


# ── Threads DM (messaging) auto-reply ──────────────────────────────────────


class ThreadsAutoReplyConfig(BaseModel):
    enabled: bool = False
    system_prompt: str = "You are a helpful assistant for {account_name}. Reply concisely and professionally."
    model: str = "@cf/meta/llama-3.1-8b-instruct"
    fallback_text: str = "Thanks for your message! I'll get back to you soon."
    max_tokens: int = 250
    cooldown_seconds: int = 300
    temperature: float = 0.7


@router.get("/{account_id}/dm/auto-reply")
async def get_threads_dm_auto_reply(
    account_id: uuid.UUID,
    team_id: TeamId,
    db: AsyncSession = Depends(get_db),
):
    """Get the Threads DM auto-reply configuration for an account."""
    account = await _get_threads_account(db, team_id, account_id)
    meta = account.meta_data or {}
    config = meta.get("threads_auto_reply", {})
    return ThreadsAutoReplyConfig(
        enabled=config.get("enabled", False),
        system_prompt=config.get("system_prompt", ThreadsAutoReplyConfig().system_prompt),
        model=config.get("model", "@cf/meta/llama-3.1-8b-instruct"),
        fallback_text=config.get("fallback_text", ThreadsAutoReplyConfig().fallback_text),
        max_tokens=config.get("max_tokens", 250),
        cooldown_seconds=config.get("cooldown_seconds", 300),
        temperature=config.get("temperature", 0.7),
    )


@router.put("/{account_id}/dm/auto-reply")
async def update_threads_dm_auto_reply(
    account_id: uuid.UUID,
    body: ThreadsAutoReplyConfig,
    team_id: TeamId,
    db: AsyncSession = Depends(get_db),
):
    """Update the Threads DM auto-reply configuration for an account."""
    account = await _get_threads_account(db, team_id, account_id)
    meta = account.meta_data or {}
    meta["threads_auto_reply"] = body.model_dump()
    account.meta_data = meta
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(account, "meta_data")
    await db.commit()
    return body


@router.get("/{account_id}/dm/conversations")
async def list_threads_dm_conversations(
    account_id: uuid.UUID,
    team_id: TeamId,
    db: AsyncSession = Depends(get_db),
):
    """List Threads DM conversations via the browser bridge."""
    await _get_threads_account(db, team_id, account_id)
    from app.services.browser_bridge import BrowserBridgeClient, BrowserBridgeError

    bridge = BrowserBridgeClient("http://browser-novnc:9223")
    try:
        result = await bridge.get_threads_dm_conversations()
        return result
    except BrowserBridgeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/{account_id}/dm/threads/{thread_id}")
async def read_threads_dm_thread(
    account_id: uuid.UUID,
    thread_id: str,
    team_id: TeamId,
    db: AsyncSession = Depends(get_db),
):
    """Read messages in a Threads DM thread via the browser bridge."""
    await _get_threads_account(db, team_id, account_id)
    from app.services.browser_bridge import BrowserBridgeClient, BrowserBridgeError

    bridge = BrowserBridgeClient("http://browser-novnc:9223")
    try:
        result = await bridge.get_threads_dm_messages(thread_id)
        return result
    except BrowserBridgeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/{account_id}/dm/threads/{thread_id}/send")
async def send_threads_dm(
    account_id: uuid.UUID,
    thread_id: str,
    body: dict,
    team_id: TeamId,
    db: AsyncSession = Depends(get_db),
):
    """Send a message in a Threads DM thread via the browser bridge."""
    await _get_threads_account(db, team_id, account_id)
    text = body.get("text")
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    from app.services.browser_bridge import BrowserBridgeClient, BrowserBridgeError

    bridge = BrowserBridgeClient("http://browser-novnc:9223")
    try:
        result = await bridge.send_threads_dm_message(thread_id, text)
        return result
    except BrowserBridgeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


# ── Threads Login Helper ───────────────────────────────────────────────
# Threads doesn't have a DM API. We use browser automation (same as
# personal Facebook Messenger, LinkedIn, and Twitter). These endpoints
# help manage the Threads browser session.

@router.post("/{account_id}/dm/login")
async def threads_dm_login(
    account_id: str,
    team_id: TeamId,
    db: AsyncSession = Depends(get_db),
):
    """Open Threads in the browser bridge for manual login.

    Navigates to threads.com and waits for the user to log in via noVNC.
    The session is saved automatically by the browser bridge.
    """
    from app.services.browser_bridge import BrowserBridgeClient, BrowserBridgeError

    account = await _get_threads_account(db, team_id, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Threads account not found")

    bridge = BrowserBridgeClient()
    try:
        # Navigate to Threads login page
        await bridge.navigate("https://www.threads.com/login")
        return {
            "status": "ok",
            "message": "Threads login page opened in browser bridge. Complete login via noVNC (port 6080).",
            "novnc_url": "http://localhost:6080/vnc.html",
            "threads_url": "https://www.threads.com/login",
        }
    except BrowserBridgeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/{account_id}/dm/session-status")
async def threads_dm_session_status(
    account_id: str,
    team_id: TeamId,
    db: AsyncSession = Depends(get_db),
):
    """Check if the Threads browser session is active."""
    from app.services.browser_bridge import BrowserBridgeClient

    account = await _get_threads_account(db, team_id, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Threads account not found")

    bridge = BrowserBridgeClient()
    try:
        status = await bridge.get_session_status()
        # Also check if we're on threads.com
        try:
            await bridge.navigate("https://www.threads.com/")
            import asyncio
            await asyncio.sleep(3)
            result = await bridge.evaluate("""() => {
                const url = window.location.href;
                const loggedIn = !!document.querySelector(
                    'a[href*="/compose"], ' +
                    'div[aria-label="Compose"], ' +
                    'button[aria-label*="Compose"]'
                );
                return { url: url, logged_in: loggedIn };
            }""")
            return {
                "browser_session": status,
                "threads_session": result,
            }
        except Exception:
            return {
                "browser_session": status,
                "threads_session": {"logged_in": False, "error": "Could not check Threads session"},
            }
    except Exception as exc:
        return {
            "browser_session": {"error": str(exc)[:200]},
            "threads_session": {"logged_in": False},
        }
