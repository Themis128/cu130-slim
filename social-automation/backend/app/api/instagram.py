"""Instagram API router — comment management, publishing quota, stories, mentions.

Exposes the new Instagram Graph API features:
- Publishing quota check (24h limit)
- Comment management (list, reply, hide, delete)
- Story publishing with links and alt text
- Tagged media / brand mentions tracking
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.core.security import decrypt_token
from app.db.session import get_db
from app.models.social_account import SocialAccount
from app.models.user import Team, TeamMember, User
from app.services.instagram_api import InstagramAPIClient, InstagramAPIError
from app.services.meta_graph import facebook_graph_url

router = APIRouter()


async def _get_team(db: AsyncSession, user: User) -> Team | None:
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == user.id)
    )
    return result.scalars().first()


async def _get_ig_client(
    db: AsyncSession,
    team: Team,
    account_id: uuid.UUID,
) -> InstagramAPIClient:
    """Resolve an Instagram account and return an authenticated client."""
    acct = (
        await db.execute(
            select(SocialAccount).where(
                SocialAccount.id == account_id,
                SocialAccount.team_id == team.id,
                SocialAccount.platform == "instagram",
            )
        )
    ).scalar_one_or_none()
    if not acct:
        raise HTTPException(status_code=404, detail="Instagram account not found")
    token = decrypt_token(acct.access_token_enc)
    return InstagramAPIClient(access_token=token, ig_user_id=acct.account_id)


# ── Response models ───────────────────────────────────────────────────────────


class QuotaResponse(BaseModel):
    remaining: int
    total: int = 25
    used: int = 0


class CommentOut(BaseModel):
    id: str
    text: str | None = None
    username: str | None = None
    timestamp: str | None = None
    like_count: int = 0


class CommentListResponse(BaseModel):
    comments: list[CommentOut]


class ReplyRequest(BaseModel):
    message: str


class CommentActionResponse(BaseModel):
    success: bool
    detail: str = ""


class StoryPublishRequest(BaseModel):
    media_url: str
    media_type: str = "IMAGE"  # IMAGE or VIDEO
    link: str | None = None
    alt_text: str | None = None


class StoryPublishResponse(BaseModel):
    media_id: str


class MentionOut(BaseModel):
    id: str
    caption: str | None = None
    media_type: str | None = None
    media_url: str | None = None
    permalink: str | None = None
    timestamp: str | None = None
    username: str | None = None


class MentionsResponse(BaseModel):
    mentions: list[MentionOut]


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/quota", response_model=QuotaResponse)
async def get_publishing_quota(
    account_id: uuid.UUID = Query(..., description="Instagram social account ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Check the 24-hour content publishing limit for an Instagram account."""
    team = await _get_team(db, current_user)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    client = await _get_ig_client(db, team, account_id)
    try:
        remaining = await client.get_remaining_publish_quota()
        data = await client.get_publishing_limit()
        usage_data = (data.get("data") or [{}])[0]
        used = 0
        for item in (usage_data.get("quota_usage") or []):
            if item.get("metric") == "publish_count":
                used = int(item.get("value", 0))
                break
        config = usage_data.get("config") or {}
        total = int(config.get("quota_total", 25))
    except InstagramAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.safe_detail)
    return QuotaResponse(remaining=remaining, total=total, used=used)


@router.get("/comments/{media_id}", response_model=CommentListResponse)
async def list_comments(
    media_id: str,
    account_id: uuid.UUID = Query(..., description="Instagram social account ID"),
    limit: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List comments on a published Instagram media object."""
    team = await _get_team(db, current_user)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    client = await _get_ig_client(db, team, account_id)
    try:
        result = await client.list_comments(media_id, limit=limit)
    except InstagramAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.safe_detail)
    comments = [
        CommentOut(
            id=str(c.get("id", "")),
            text=c.get("text"),
            username=c.get("username"),
            timestamp=c.get("timestamp"),
            like_count=int(c.get("like_count", 0) or 0),
        )
        for c in result.get("data") or []
    ]
    return CommentListResponse(comments=comments)


@router.post("/comments/{comment_id}/reply", response_model=CommentOut)
async def reply_to_comment(
    comment_id: str,
    request: ReplyRequest,
    account_id: uuid.UUID = Query(..., description="Instagram social account ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Reply to an existing Instagram comment."""
    team = await _get_team(db, current_user)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    client = await _get_ig_client(db, team, account_id)
    try:
        result = await client.reply_to_comment(comment_id, request.message)
    except InstagramAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.safe_detail)
    return CommentOut(
        id=str(result.get("id", "")),
        text=result.get("text"),
        username=result.get("username"),
        timestamp=result.get("timestamp"),
    )


@router.post("/comments/{comment_id}/hide", response_model=CommentActionResponse)
async def hide_comment(
    comment_id: str,
    account_id: uuid.UUID = Query(..., description="Instagram social account ID"),
    hide: bool = Query(True),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Hide or unhide an Instagram comment."""
    team = await _get_team(db, current_user)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    client = await _get_ig_client(db, team, account_id)
    try:
        await client.hide_comment(comment_id, hide=hide)
    except InstagramAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.safe_detail)
    return CommentActionResponse(success=True, detail="hidden" if hide else "unhidden")


@router.delete("/comments/{comment_id}", response_model=CommentActionResponse)
async def delete_comment(
    comment_id: str,
    account_id: uuid.UUID = Query(..., description="Instagram social account ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete an Instagram comment."""
    team = await _get_team(db, current_user)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    client = await _get_ig_client(db, team, account_id)
    try:
        await client.delete_comment(comment_id)
    except InstagramAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.safe_detail)
    return CommentActionResponse(success=True, detail="deleted")


@router.post("/stories", response_model=StoryPublishResponse)
async def publish_story(
    request: StoryPublishRequest,
    account_id: uuid.UUID = Query(..., description="Instagram social account ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Publish an Instagram story with optional link and alt text."""
    team = await _get_team(db, current_user)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    client = await _get_ig_client(db, team, account_id)
    try:
        media_id = await client.publish_story(
            media_url=request.media_url,
            media_type=request.media_type,
            link=request.link,
            alt_text=request.alt_text,
        )
    except InstagramAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.safe_detail)
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=exc.safe_detail)
    return StoryPublishResponse(media_id=media_id)


@router.get("/mentions", response_model=MentionsResponse)
async def get_mentions(
    account_id: uuid.UUID = Query(..., description="Instagram social account ID"),
    limit: int = Query(10, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch media where the Instagram account is tagged (brand mentions/UGC)."""
    team = await _get_team(db, current_user)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    client = await _get_ig_client(db, team, account_id)
    try:
        mentions = await client.get_recent_mentions(limit=limit)
    except InstagramAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.safe_detail)
    return MentionsResponse(
        mentions=[
            MentionOut(
                id=str(m.get("id", "")),
                caption=m.get("caption"),
                media_type=m.get("media_type"),
                media_url=m.get("media_url"),
                permalink=m.get("permalink"),
                timestamp=m.get("timestamp"),
                username=m.get("username"),
            )
            for m in mentions
        ]
    )


# ── Instagram App Review Helper ────────────────────────────────────────
# Meta requires App Review for the instagram_business_manage_messages
# permission before an app can read/send Instagram DMs via the API.
# This endpoint generates the required submission text, screencast
# description, and test data for the App Review submission.

@router.get("/app-review/guide")
async def get_app_review_guide():
    """Get the App Review submission guide for instagram_business_manage_messages.

    Returns the required text, screencast description, and test data
    for submitting the App Review request in the Meta App Dashboard.
    """
    return {
        "permission": "instagram_business_manage_messages",
        "permission_display_name": "Instagram Business - Manage Messages",
        "submission_steps": [
            "1. Go to Meta App Dashboard > App Review > Requests",
            "2. Click 'Request Permissions or Features'",
            "3. Select 'instagram_business_manage_messages'",
            "4. Copy the description below into the submission form",
            "5. Record a screencast showing your app receiving and replying to a DM",
            "6. Submit for review (typically takes 1-7 business days)",
        ],
        "description_text": (
            "SocialAuto is a self-hosted social media management platform that "
            "helps businesses manage their Instagram DMs alongside other social "
            "channels. The instagram_business_manage_messages permission is used "
            "to:\n\n"
            "1. Read incoming DM conversations from our Instagram Business account\n"
            "2. Send AI-assisted auto-replies to common customer inquiries\n"
            "3. Mark conversations as read after processing\n"
            "4. Show typing indicators for a natural conversation flow\n\n"
            "Our app processes messages on behalf of our own business only (not "
            "other businesses). We use the Send API to reply within the 24-hour "
            "messaging window and respect all Meta Platform policies.\n\n"
            "How to test:\n"
            "- Send a DM to our Instagram Business account (@cloudless.gr)\n"
            "- The app will read the message via the Conversations API\n"
            "- The app generates a contextual reply using our AI service\n"
            "- The app sends the reply via the Send API\n"
            "- The conversation is marked as read\n\n"
            "Data handling:\n"
            "- Messages are processed in real-time and not stored permanently\n"
            "- No data is shared with third parties\n"
            "- Users can opt out of auto-reply at any time"
        ),
        "screencast_description": (
            "The screencast should show:\n"
            "1. A user sending a DM to our Instagram Business account (@cloudless.gr)\n"
            "2. The SocialAuto dashboard showing the incoming message\n"
            "3. The app generating an AI-assisted reply\n"
            "4. The reply being sent via the Send API\n"
            "5. The user receiving the reply in their Instagram DM\n"
            "6. The conversation being marked as read"
        ),
        "test_data": {
            "instagram_username": "cloudless.gr",
            "test_message": "Hi! What services do you offer?",
            "expected_reply": "Hi! Thanks for reaching out. We're Cloudless — a self-hosted social media automation platform. How can we help you today?",
        },
        "required_permissions": [
            "instagram_business_basic",
            "instagram_business_manage_messages",
        ],
        "documentation_links": {
            "app_review": "https://developers.facebook.com/docs/instagram-platform/app-review/",
            "messenger_platform_instagram": "https://developers.facebook.com/docs/messenger-platform/instagram/app-review/",
            "send_api": "https://developers.facebook.com/docs/business-messaging/instagram-messaging/features/send-message",
        },
    }


@router.get("/app-review/status")
async def get_app_review_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Check the App Review status for Instagram messaging permissions.

    Returns the current status of the instagram_business_manage_messages
    permission for this app, based on the API response when trying to
    access the conversations endpoint.
    """
    import httpx

    result = await db.execute(
        select(SocialAccount).where(
            SocialAccount.platform == "instagram",
            SocialAccount.status == "active",
        )
    )
    accounts = result.scalars().all()

    statuses = []
    for account in accounts:
        status = {
            "account_id": str(account.id),
            "display_name": account.display_name,
            "has_token": bool(account.access_token_enc),
            "permission_status": "unknown",
        }

        if account.access_token_enc:
            try:
                token = decrypt_token(account.access_token_enc)
                ig_user_id = account.account_id or ""

                if ig_user_id:
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        resp = await client.get(
                            facebook_graph_url(f"{ig_user_id}/conversations"),
                            params={
                                "platform": "instagram",
                                "access_token": token,
                                "fields": "id",
                                "limit": 1,
                            },
                        )

                    if resp.status_code == 200:
                        status["permission_status"] = "granted"
                    elif resp.status_code == 400:
                        error_data = resp.json()
                        error_code = error_data.get("error", {}).get("code", 0)
                        if error_code == 3:
                            status["permission_status"] = "not_granted"
                            status["error"] = "Application does not have the capability to make this API call"
                        elif error_code == 10:
                            status["permission_status"] = "permission_denied"
                            status["error"] = "Permission not granted"
                        else:
                            status["permission_status"] = "error"
                            status["error"] = error_data.get("error", {}).get("message", "")
                    elif resp.status_code == 403:
                        status["permission_status"] = "forbidden"
                        status["error"] = "Access forbidden"
                    else:
                        status["permission_status"] = "error"
                        status["error"] = f"HTTP {resp.status_code}"
            except Exception as exc:
                status["permission_status"] = "error"
                status["error"] = str(exc)[:200]

        statuses.append(status)

    return {
        "permission": "instagram_business_manage_messages",
        "accounts": statuses,
        "needs_app_review": any(s["permission_status"] == "not_granted" for s in statuses),
        "guide_endpoint": "/api/v1/instagram/app-review/guide",
    }
