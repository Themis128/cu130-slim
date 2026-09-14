"""TikTok management API — Content Posting + Display API wrappers.

Endpoints follow official TikTok for Developers docs:

- Creator info: POST /v2/post/publish/creator_info/query/
  https://developers.tiktok.com/doc/content-posting-api-reference-query-creator-info
- Publish status: POST /v2/post/publish/status/fetch/
  https://developers.tiktok.com/doc/content-posting-api-reference-get-video-status
- Cancel publish: POST /v2/post/publish/cancel/
  https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide
- List videos: POST /v2/video/list/?fields=...
  https://developers.tiktok.com/doc/tiktok-api-v2-video-list
- Query videos: POST /v2/video/query/?fields=...
  https://developers.tiktok.com/doc/tiktok-api-v2-video-query
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.auth import get_current_user
from app.core.security import decrypt_token
from app.db.session import get_db
from app.models.content import Post, PostTarget
from app.models.social_account import SocialAccount
from app.models.user import Team, TeamMember, User
from app.services.tiktok_api import TikTokAPIClient, TikTokAPIError
from app.services.tiktok_browser import TikTokBrowserService

logger = logging.getLogger(__name__)
router = APIRouter()

# Official Display API max_count ceiling
_MAX_VIDEO_PAGE = 20

# Statuses that still occupy a pending inbox share (MEDIA_UPLOAD flow)
_PENDING_STATUSES = frozenset({"PROCESSING_UPLOAD", "PROCESSING_DOWNLOAD", "SEND_TO_USER_INBOX"})


async def _get_tiktok_account(
    db: AsyncSession,
    account_id: uuid.UUID,
    user: User,
) -> SocialAccount:
    result = await db.execute(
        select(SocialAccount)
        .join(Team)
        .join(TeamMember)
        .where(
            SocialAccount.id == account_id,
            TeamMember.user_id == user.id,
            SocialAccount.platform == "tiktok",
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="TikTok account not found")
    return account


def _client_for(account: SocialAccount) -> TikTokAPIClient:
    token = decrypt_token(account.access_token_enc)
    open_id = (account.meta_data or {}).get("open_id") or account.account_id
    return TikTokAPIClient(access_token=token, open_id=open_id)


def _http_exc(exc: TikTokAPIError) -> HTTPException:
    return HTTPException(status_code=exc.status_code or 400, detail=str(exc))


# ── Response models (map official TikTok fields) ─────────────────────────────


class CreatorInfoOut(BaseModel):
    """Maps /v2/post/publish/creator_info/query/ response.data."""

    creator_avatar_url: str | None = None
    creator_username: str | None = None
    creator_nickname: str | None = None
    privacy_level_options: list[str] = Field(default_factory=list)
    comment_disabled: bool | None = None
    duet_disabled: bool | None = None
    stitch_disabled: bool | None = None
    max_video_post_duration_sec: int | None = None


class TikTokVideoOut(BaseModel):
    """Maps Display API Video Object fields we request."""

    id: str
    create_time: int | None = None
    cover_image_url: str | None = None
    share_url: str | None = None
    video_description: str | None = None
    duration: int | None = None
    title: str | None = None
    like_count: int = 0
    comment_count: int = 0
    share_count: int = 0
    view_count: int = 0


class TikTokVideoListOut(BaseModel):
    videos: list[TikTokVideoOut]
    cursor: int = 0
    has_more: bool = False


class PublishStatusOut(BaseModel):
    """Maps /v2/post/publish/status/fetch/ response.data."""

    publish_id: str
    status: str | None = None
    fail_reason: str | None = None
    publicaly_available_post_id: list[int] = Field(default_factory=list)
    uploaded_bytes: int | None = None
    downloaded_bytes: int | None = None


class PublishIdIn(BaseModel):
    publish_id: str = Field(..., min_length=1, max_length=200)


class QueryVideosIn(BaseModel):
    video_ids: list[str] = Field(..., min_length=1, max_length=20)


class UploadRecordOut(BaseModel):
    publish_id: str
    post_id: str | None = None
    target_id: str | None = None
    status: str | None = None
    fail_reason: str | None = None
    created_at: datetime | None = None
    is_pending: bool = False


class TikTokHealthOut(BaseModel):
    account_id: str
    username: str | None = None
    status: str
    scopes: list[str] = Field(default_factory=list)
    token_expires_at: datetime | None = None
    token_valid: bool = False
    has_video_list_scope: bool = False
    has_video_publish_scope: bool = False
    creator: CreatorInfoOut | None = None
    sidecar_session: dict | None = None
    pending_uploads_24h: int = 0


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/accounts/{account_id}/health", response_model=TikTokHealthOut)
async def tiktok_health(
    account_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Token + creator + sidecar health for TikTok management UI."""
    account = await _get_tiktok_account(db, account_id, current_user)
    scopes = list(account.scopes or [])
    client = _client_for(account)

    token_valid = False
    creator: CreatorInfoOut | None = None
    try:
        await client.validate_token()
        token_valid = True
    except TikTokAPIError:
        token_valid = False

    if token_valid and "video.publish" in scopes:
        try:
            raw = await client.get_creator_info()
            data = raw.get("data") or {}
            creator = CreatorInfoOut(**{k: data.get(k) for k in CreatorInfoOut.model_fields})
        except TikTokAPIError as exc:
            logger.info("creator_info unavailable: %s", exc)

    sidecar: dict | None = None
    browser: TikTokBrowserService | None = None
    try:
        browser = TikTokBrowserService()
        sidecar = await browser.check_session()
    except Exception as exc:  # noqa: BLE001 — sidecar optional
        sidecar = {"status": "unreachable", "error": str(exc)[:200]}
    finally:
        if browser is not None:
            await browser.close()

    since = datetime.now(UTC) - timedelta(hours=24)
    pending_q = await db.execute(
        select(PostTarget)
        .join(Post)
        .where(
            PostTarget.social_account_id == account.id,
            PostTarget.platform_post_id.isnot(None),
            Post.created_at >= since,
        )
    )
    recent_targets = pending_q.scalars().all()
    # Heuristic: count recent TikTok publish_ids; live status checked via /uploads
    pending_uploads_24h = len(recent_targets)

    return TikTokHealthOut(
        account_id=str(account.id),
        username=account.username,
        status=account.status,
        scopes=scopes,
        token_expires_at=account.token_expires_at,
        token_valid=token_valid,
        has_video_list_scope="video.list" in scopes,
        has_video_publish_scope="video.publish" in scopes,
        creator=creator,
        sidecar_session=sidecar,
        pending_uploads_24h=pending_uploads_24h,
    )


@router.get("/accounts/{account_id}/creator-info", response_model=CreatorInfoOut)
async def tiktok_creator_info(
    account_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Official Query Creator Info — required before DIRECT_POST UX."""
    account = await _get_tiktok_account(db, account_id, current_user)
    try:
        raw = await _client_for(account).get_creator_info()
    except TikTokAPIError as exc:
        raise _http_exc(exc) from exc
    data = raw.get("data") or {}
    return CreatorInfoOut(**{k: data.get(k) for k in CreatorInfoOut.model_fields})


@router.get("/accounts/{account_id}/videos", response_model=TikTokVideoListOut)
async def tiktok_list_videos(
    account_id: uuid.UUID,
    cursor: int = Query(0, ge=0, description="UTC Unix ms cursor from previous page"),
    max_count: int = Query(20, ge=1, le=_MAX_VIDEO_PAGE),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Official Display API video list (scope: video.list, max 20/page)."""
    account = await _get_tiktok_account(db, account_id, current_user)
    if "video.list" not in (account.scopes or []):
        raise HTTPException(
            status_code=400,
            detail="Account missing video.list scope — reconnect TikTok from Channels",
        )
    try:
        raw = await _client_for(account).list_videos(cursor=cursor, max_count=max_count)
    except TikTokAPIError as exc:
        raise _http_exc(exc) from exc

    data = raw.get("data") or {}
    videos = [
        TikTokVideoOut(
            id=str(v.get("id", "")),
            create_time=v.get("create_time"),
            cover_image_url=v.get("cover_image_url"),
            share_url=v.get("share_url"),
            video_description=v.get("video_description"),
            duration=v.get("duration"),
            title=v.get("title"),
            like_count=int(v.get("like_count", 0) or 0),
            comment_count=int(v.get("comment_count", 0) or 0),
            share_count=int(v.get("share_count", 0) or 0),
            view_count=int(v.get("view_count", 0) or 0),
        )
        for v in (data.get("videos") or [])
    ]
    return TikTokVideoListOut(
        videos=videos,
        cursor=int(data.get("cursor", cursor) or cursor),
        has_more=bool(data.get("has_more")),
    )


@router.post("/accounts/{account_id}/videos/query", response_model=TikTokVideoListOut)
async def tiktok_query_videos(
    account_id: uuid.UUID,
    body: QueryVideosIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Official Display API video query by IDs (max 20)."""
    account = await _get_tiktok_account(db, account_id, current_user)
    if "video.list" not in (account.scopes or []):
        raise HTTPException(
            status_code=400,
            detail="Account missing video.list scope — reconnect TikTok from Channels",
        )
    try:
        raw = await _client_for(account).query_video(video_ids=body.video_ids)
    except TikTokAPIError as exc:
        raise _http_exc(exc) from exc

    data = raw.get("data") or {}
    videos = [
        TikTokVideoOut(
            id=str(v.get("id", "")),
            create_time=v.get("create_time"),
            cover_image_url=v.get("cover_image_url"),
            share_url=v.get("share_url"),
            video_description=v.get("video_description"),
            duration=v.get("duration"),
            title=v.get("title"),
            like_count=int(v.get("like_count", 0) or 0),
            comment_count=int(v.get("comment_count", 0) or 0),
            share_count=int(v.get("share_count", 0) or 0),
            view_count=int(v.get("view_count", 0) or 0),
        )
        for v in (data.get("videos") or [])
    ]
    return TikTokVideoListOut(videos=videos, cursor=0, has_more=False)


@router.post("/accounts/{account_id}/publish/status", response_model=PublishStatusOut)
async def tiktok_publish_status(
    account_id: uuid.UUID,
    body: PublishIdIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Official Get Post Status — poll PROCESSING_* / SEND_TO_USER_INBOX / COMPLETE / FAILED."""
    account = await _get_tiktok_account(db, account_id, current_user)
    try:
        raw = await _client_for(account).check_publish_status(body.publish_id)
    except TikTokAPIError as exc:
        raise _http_exc(exc) from exc
    data = raw.get("data") or {}
    return PublishStatusOut(
        publish_id=body.publish_id,
        status=data.get("status"),
        fail_reason=data.get("fail_reason"),
        publicaly_available_post_id=list(data.get("publicaly_available_post_id") or []),
        uploaded_bytes=data.get("uploaded_bytes"),
        downloaded_bytes=data.get("downloaded_bytes"),
    )


@router.post("/accounts/{account_id}/publish/cancel")
async def tiktok_publish_cancel(
    account_id: uuid.UUID,
    body: PublishIdIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Official cancel for ongoing PULL_FROM_URL / upload (best-effort)."""
    account = await _get_tiktok_account(db, account_id, current_user)
    try:
        raw = await _client_for(account).cancel_publish(body.publish_id)
    except TikTokAPIError as exc:
        raise _http_exc(exc) from exc
    error = raw.get("error") or {}
    return {
        "ok": error.get("code") == "ok",
        "publish_id": body.publish_id,
        "error_code": error.get("code"),
        "message": error.get("message") or "",
        "log_id": error.get("log_id"),
    }


@router.get("/accounts/{account_id}/uploads", response_model=list[UploadRecordOut])
async def tiktok_list_uploads(
    account_id: uuid.UUID,
    hours: int = Query(24, ge=1, le=168, description="Look-back window for SocialAuto publish_ids"),
    live_status: bool = Query(True, description="Poll TikTok status/fetch for each publish_id"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List recent TikTok publish_ids from SocialAuto posts and optionally poll live status.

    TikTok does not expose a pending-shares list API; this combines our PostTarget
    records with official status/fetch (and cancel when needed). Helps avoid
    spam_risk_too_many_pending_share (5 pending / 24h).
    """
    account = await _get_tiktok_account(db, account_id, current_user)
    since = datetime.now(UTC) - timedelta(hours=hours)
    result = await db.execute(
        select(PostTarget)
        .options(selectinload(PostTarget.post))
        .join(Post)
        .where(
            PostTarget.social_account_id == account.id,
            PostTarget.platform_post_id.isnot(None),
            Post.created_at >= since,
        )
        .order_by(Post.created_at.desc())
        .limit(50)
    )
    targets = result.scalars().all()
    client = _client_for(account) if live_status else None
    from app.services.tiktok_api import is_tiktok_publish_id

    out: list[UploadRecordOut] = []
    for t in targets:
        ps = (getattr(t.post, "platform_specific", None) or {}).get("tiktok") or {}
        publish_id = str(ps.get("publish_id") or t.platform_post_id or "").strip()
        status = None
        fail_reason = None
        # Only poll Content Posting status for real publish_ids (not Display video ids).
        if client and publish_id and is_tiktok_publish_id(publish_id):
            try:
                raw = await client.check_publish_status(publish_id)
                data = raw.get("data") or {}
                status = data.get("status")
                fail_reason = data.get("fail_reason")
            except TikTokAPIError as exc:
                status = "LOOKUP_FAILED"
                fail_reason = str(exc)[:200]
        elif publish_id and not is_tiktok_publish_id(publish_id):
            status = "PUBLISH_COMPLETE"
        out.append(
            UploadRecordOut(
                publish_id=publish_id,
                post_id=str(t.post_id) if t.post_id else None,
                target_id=f"{t.post_id}:{t.social_account_id}",
                status=status,
                fail_reason=fail_reason,
                created_at=getattr(t.post, "created_at", None) if t.post else None,
                is_pending=bool(status in _PENDING_STATUSES),
            )
        )
    return out
