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
