import re
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.auth import get_current_user, log_action, require_admin, require_editor
from app.api.deps import TeamId
from app.db.session import get_db
from app.models.content import ContentBrief, Pillar, Post, PostComment, PostStatus, PostTarget, RecurrencePattern
from app.models.social_account import SocialAccount
from app.models.user import Team, TeamMember, User
from app.services.content_renderer import render_post_text
from app.services.spellcheck import auto_correct

router = APIRouter()


class PostCreate(BaseModel):
    content_text: str | None = None
    media_ids: list[uuid.UUID] = []
    platform_specific: dict = {}
    hashtags: list[str] = []
    mention_accounts: list[str] = []
    link_url: str | None = None
    link_preview_override: dict | None = None
    scheduled_at: datetime | None = None
    target_account_ids: list[uuid.UUID] = []
    metadata: dict = {}
    music_asset_id: uuid.UUID | None = None
    pillar_id: uuid.UUID | None = None
    content_brief_id: uuid.UUID | None = None
    # Recurring post fields
    is_recurring: bool = False
    recurrence_pattern: RecurrencePattern = RecurrencePattern.NONE
    recurrence_interval: int = 0  # N days/weeks/months
    recurrence_max: int = 0  # 0 = unlimited

class PostUpdate(BaseModel):
    content_text: str | None = None
    media_ids: list[uuid.UUID] | None = None
    platform_specific: dict | None = None
    hashtags: list[str] | None = None
    mention_accounts: list[str] | None = None
    link_url: str | None = None
    link_preview_override: dict | None = None
    scheduled_at: datetime | None = None
    target_account_ids: list[uuid.UUID] | None
    metadata: dict | None = None
    music_asset_id: uuid.UUID | None = None
    pillar_id: uuid.UUID | None = None
    content_brief_id: uuid.UUID | None = None
    # Recurring post fields
    is_recurring: bool | None = None
    recurrence_pattern: RecurrencePattern | None = None
    recurrence_interval: int | None = None
    recurrence_max: int | None = None


class PostResponse(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID
    user_id: uuid.UUID | None
    status: PostStatus
    content_text: str | None
    media_ids: list[uuid.UUID]
    platform_specific: dict
    hashtags: list[str]
    mention_accounts: list[str]
    link_url: str | None
    link_preview_override: dict | None
    scheduled_at: datetime | None
    published_at: datetime | None
    failed_at: datetime | None
    failure_reason: str | None
    workflow_id: uuid.UUID | None
    workflow_run_id: str | None
    metadata: dict
    music_asset_id: uuid.UUID | None = None
    pillar_id: uuid.UUID | None = None
    content_brief_id: uuid.UUID | None = None
    # Recurring post fields
    is_recurring: bool = False
    recurrence_pattern: RecurrencePattern = RecurrencePattern.NONE
    recurrence_interval: int = 0
    recurrence_count: int = 0
    recurrence_max: int = 0
    recurrence_parent_id: uuid.UUID | None = None
    next_recurrence_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    targets: list[dict] = []
    comments: list[dict] = []

    class Config:
        from_attributes = True


class PostListResponse(BaseModel):
    posts: list[PostResponse]
    total: int
    page: int
    page_size: int


@router.post("/posts", response_model=PostResponse, status_code=status.HTTP_201_CREATED)
async def create_post(post_data: PostCreate, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    # Resolve the user's team (single-team model)
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()
    if not team:
        raise HTTPException(status_code=400, detail="No team found")

    corrected_text = await auto_correct(post_data.content_text or "")

    post = Post(
        team_id=team.id,
        user_id=current_user.id,
        status=PostStatus.DRAFT if not post_data.scheduled_at else PostStatus.SCHEDULED,
        content_text=corrected_text or post_data.content_text,
        media_ids=post_data.media_ids,
        platform_specific=post_data.platform_specific,
        hashtags=post_data.hashtags,
        mention_accounts=post_data.mention_accounts,
        link_url=post_data.link_url,
        link_preview_override=post_data.link_preview_override,
        scheduled_at=post_data.scheduled_at,
        meta_data=post_data.metadata,
        music_asset_id=post_data.music_asset_id,
        pillar_id=post_data.pillar_id,
        content_brief_id=post_data.content_brief_id,
        is_recurring=post_data.is_recurring,
        recurrence_pattern=post_data.recurrence_pattern,
        recurrence_interval=post_data.recurrence_interval,
        recurrence_max=post_data.recurrence_max,
    )
    db.add(post)
    await db.flush()

    # Create post targets
    for account_id in post_data.target_account_ids:
        target = PostTarget(post_id=post.id, social_account_id=account_id)
        db.add(target)

    await db.commit()
    await db.refresh(post)

    return await _post_to_response(post, db)


@router.get("/posts", response_model=PostListResponse)
async def list_posts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: PostStatus | None = None,
    search: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()
    if not team:
        return PostListResponse(posts=[], total=0, page=page, page_size=page_size)

    query = select(Post).where(Post.team_id == team.id).options(selectinload(Post.targets).selectinload(PostTarget.social_account))

    if status:
        query = query.where(Post.status == status)
    if search:
        query = query.where(Post.content_text.ilike(f"%{search}%"))

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query) or 0

    # Paginate
    query = query.order_by(Post.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    posts = result.scalars().all()

    post_responses = [await _post_to_response(p, db) for p in posts]

    return PostListResponse(posts=post_responses, total=total, page=page, page_size=page_size)


@router.get("/posts/calendar", response_model=list[dict])
async def get_calendar(
    start: datetime,
    end: datetime,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()
    if not team:
        return []

    result = await db.execute(
        select(Post)
        .where(
            Post.team_id == team.id,
            Post.scheduled_at >= start,
            Post.scheduled_at <= end,
            Post.status.in_([PostStatus.SCHEDULED, PostStatus.PUBLISHED, PostStatus.APPROVED, PostStatus.REVIEW]),
        )
        .options(selectinload(Post.targets).selectinload(PostTarget.social_account))
    )
    posts = result.scalars().all()

    return [
        {
            "id": str(p.id),
            "title": (p.content_text[:50] + "...") if p.content_text and len(p.content_text) > 50 else p.content_text or "Untitled",
            "start": p.scheduled_at.isoformat() if p.scheduled_at else p.created_at.isoformat(),
            "status": p.status.value,
            "platforms": [t.social_account.platform for t in p.targets],
            "pillar_id": str(p.pillar_id) if p.pillar_id else None,
        }
        for p in posts
    ]


@router.get("/posts/{post_id}", response_model=PostResponse)
async def get_post(post_id: uuid.UUID, team_id: TeamId, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Post)
        .options(selectinload(Post.targets).selectinload(PostTarget.social_account))
        .where(Post.id == post_id, Post.team_id == team_id)
    )
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    return await _post_to_response(post, db)


@router.patch("/posts/{post_id}", response_model=PostResponse)
async def update_post(post_id: uuid.UUID, post_data: PostUpdate, team_id: TeamId, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Post).where(Post.id == post_id, Post.team_id == team_id).options(selectinload(Post.targets))
    )
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    if post.status not in [PostStatus.DRAFT, PostStatus.SCHEDULED, PostStatus.REVIEW, PostStatus.APPROVED, PostStatus.PUBLISHING]:
        raise HTTPException(status_code=400, detail="Cannot edit published/failed post")
    # Allow editing posts stuck in PUBLISHING — the user may need to add
    # missing targets or fix content before retrying.

    update_data = post_data.model_dump(exclude_unset=True)
    target_account_ids = update_data.pop("target_account_ids", None)
    # Map API field name to model field name
    if "metadata" in update_data:
        update_data["meta_data"] = update_data.pop("metadata")

    if "content_text" in update_data and update_data["content_text"]:
        update_data["content_text"] = await auto_correct(update_data["content_text"])

    for field, value in update_data.items():
        setattr(post, field, value)

    if post.scheduled_at and post.status == PostStatus.DRAFT:
        post.status = PostStatus.SCHEDULED
    elif not post.scheduled_at and post.status == PostStatus.SCHEDULED:
        post.status = PostStatus.DRAFT

    if target_account_ids is not None:
        # Delete existing targets
        for target in post.targets:
            await db.delete(target)
        # Create new targets
        for account_id in target_account_ids:
            db.add(PostTarget(post_id=post.id, social_account_id=account_id))

    await db.commit()
    await db.refresh(post)

    return await _post_to_response(post, db)


@router.delete("/posts/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_post(post_id: uuid.UUID, team_id: TeamId, current_user: User = Depends(require_editor), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Post).where(Post.id == post_id, Post.team_id == team_id))
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    if post.status == PostStatus.PUBLISHED:
        raise HTTPException(status_code=400, detail="Cannot delete published post")
    # Allow deleting posts stuck in PUBLISHING — they haven't actually
    # been published yet and may be stuck due to missing targets or
    # worker errors.

    await log_action(db, user=current_user, action="delete", resource_type="post", resource_id=str(post_id), detail=(post.content_text or "")[:100])
    await db.delete(post)
    await db.commit()


@router.post("/posts/{post_id}/schedule", response_model=PostResponse)
async def schedule_post(post_id: uuid.UUID, scheduled_at: str, team_id: TeamId, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    # Parse ISO 8601 string, support Z suffix
    try:
        if scheduled_at.endswith('Z'):
            scheduled_at = scheduled_at[:-1] + '+00:00'
        dt = datetime.fromisoformat(scheduled_at)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid datetime format")

    result = await db.execute(select(Post).where(Post.id == post_id, Post.team_id == team_id))
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    post.status = PostStatus.SCHEDULED
    post.scheduled_at = dt
    await db.commit()
    await db.refresh(post)

    return await _post_to_response(post, db)


@router.post("/posts/{post_id}/publish-now", response_model=PostResponse)
async def publish_now(post_id: uuid.UUID, team_id: TeamId, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from app.worker.tasks.publishing import process_publish_queue, publish_post_now

    result = await db.execute(select(Post).where(Post.id == post_id, Post.team_id == team_id).options(selectinload(Post.targets)))
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    # Brand compliance check before publish (non-blocking — logs issues but doesn't prevent publish)
    try:
        from app.models.brand import Brand, BrandVoice
        from app.services.brand_compliance import score_brand_compliance
        brand_result = await db.execute(select(Brand).where(Brand.team_id == team_id))
        brand = brand_result.scalars().first()
        if brand:
            voice_result = await db.execute(select(BrandVoice).where(BrandVoice.brand_id == brand.id))
            voice = voice_result.scalars().first()
            brand_dict = {
                "name": brand.name,
                "positioning_statement": brand.positioning_statement,
                "mission": brand.mission,
                "values": brand.values,
            }
            voice_dict = None
            if voice:
                voice_dict = {
                    "tone_dimensions": voice.tone_dimensions,
                    "banned_phrases": voice.banned_phrases,
                    "preferred_phrases": voice.preferred_phrases,
                }
            compliance = await score_brand_compliance(
                content=post.content or "",
                brand=brand_dict,
                voice=voice_dict,
            )
            if compliance.get("score", 5) < 3:
                import structlog
                logger = structlog.get_logger()
                logger.warning("brand_compliance_low", post_id=str(post_id), score=compliance.get("score"), issues=compliance.get("issues"))
    except Exception:
        pass  # Compliance check is advisory only — never block publishing

    account_ids = [str(t.social_account_id) for t in post.targets]
    if not account_ids:
        raise HTTPException(
            status_code=400,
            detail="Cannot publish: no target accounts assigned. Add target_account_ids before publishing.",
        )

    post.status = PostStatus.SCHEDULED
    post.scheduled_at = datetime.now(UTC)
    await log_action(db, user=current_user, action="publish", resource_type="post", resource_id=str(post_id))
    await db.commit()
    await db.refresh(post)

    import asyncio
    loop = asyncio.get_event_loop()
    pid_str = str(post_id)
    loop.run_in_executor(None, lambda: publish_post_now.delay(pid_str, account_ids))
    loop.run_in_executor(None, lambda: process_publish_queue.apply_async(countdown=2))

    return await _post_to_response(post, db)


@router.post("/posts/{post_id}/duplicate", response_model=PostResponse)
async def duplicate_post(post_id: uuid.UUID, team_id: TeamId, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Post).where(Post.id == post_id, Post.team_id == team_id).options(selectinload(Post.targets)))
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    new_post = Post(
        team_id=post.team_id,
        user_id=current_user.id,
        status=PostStatus.DRAFT,
        content_text=post.content_text,
        media_ids=post.media_ids,
        platform_specific=post.platform_specific,
        hashtags=post.hashtags,
        mention_accounts=post.mention_accounts,
        link_url=post.link_url,
        link_preview_override=post.link_preview_override,
        meta_data={},
        music_asset_id=post.music_asset_id,
    )
    db.add(new_post)
    await db.flush()

    for target in post.targets:
        db.add(PostTarget(post_id=new_post.id, social_account_id=target.social_account_id))

    await db.commit()
    await db.refresh(new_post)

    return await _post_to_response(new_post, db)


class CrossPostRequest(BaseModel):
    """Request body for cross-posting an existing post to another platform."""
    target_platform: str  # e.g. "threads", "twitter", "facebook"
    target_account_id: uuid.UUID | None = None  # specific account; auto-select if omitted
    adapt_content: bool = True  # re-render text for target platform limits
    schedule_at: datetime | None = None  # schedule instead of draft


@router.post("/posts/{post_id}/cross-post", response_model=PostResponse)
async def cross_post_to_platform(
    post_id: uuid.UUID,
    request: CrossPostRequest,
    team_id: TeamId,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cross-post an existing post to another platform.

    Creates a new draft (or scheduled post) with content adapted for the
    target platform's character limits, hashtag caps, and link rules.
    Reuses the original media assets. If ``target_account_id`` is omitted,
    the first active account on ``target_platform`` for the team is used.
    """
    result = await db.execute(
        select(Post).where(Post.id == post_id, Post.team_id == team_id).options(selectinload(Post.targets))
    )
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    # Resolve team
    team_result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = team_result.scalars().first()
    if not team:
        raise HTTPException(status_code=400, detail="No team found")

    # Find the target account
    if request.target_account_id:
        acct_result = await db.execute(
            select(SocialAccount).where(
                SocialAccount.id == request.target_account_id,
                SocialAccount.team_id == team.id,
                SocialAccount.platform == request.target_platform,
                SocialAccount.status == "active",
            )
        )
        target_account = acct_result.scalar_one_or_none()
    else:
        acct_result = await db.execute(
            select(SocialAccount).where(
                SocialAccount.team_id == team.id,
                SocialAccount.platform == request.target_platform,
                SocialAccount.status == "active",
            ).limit(1)
        )
        target_account = acct_result.scalar_one_or_none()

    if not target_account:
        raise HTTPException(
            status_code=404,
            detail=f"No active {request.target_platform} account found for your team",
        )

    # Adapt content for the target platform
    if request.adapt_content:
        adapted_text = render_post_text(post, request.target_platform)
    else:
        adapted_text = post.content_text or ""

    corrected_text = await auto_correct(adapted_text)

    new_post = Post(
        team_id=team.id,
        user_id=current_user.id,
        status=PostStatus.DRAFT if not request.schedule_at else PostStatus.SCHEDULED,
        content_text=corrected_text or adapted_text,
        media_ids=post.media_ids,
        platform_specific={request.target_platform: {"content_text": corrected_text or adapted_text}},
        hashtags=post.hashtags,
        mention_accounts=post.mention_accounts,
        link_url=post.link_url,
        link_preview_override=post.link_preview_override,
        scheduled_at=request.schedule_at,
        meta_data={"cross_posted_from": str(post_id), "source_platform": post.targets[0].social_account_id if post.targets else None},
        music_asset_id=post.music_asset_id,
    )
    db.add(new_post)
    await db.flush()

    db.add(PostTarget(post_id=new_post.id, social_account_id=target_account.id))

    await db.commit()
    await db.refresh(new_post)

    return await _post_to_response(new_post, db)


async def _post_to_response(post: Post, db: AsyncSession) -> PostResponse:
    await db.refresh(post, ["targets", "comments"])
    for target in post.targets:
        await db.refresh(target, ["social_account"])

    return PostResponse(
        id=post.id,
        team_id=post.team_id,
        user_id=post.user_id,
        status=post.status,
        content_text=post.content_text,
        media_ids=post.media_ids,
        platform_specific=post.platform_specific,
        hashtags=post.hashtags,
        mention_accounts=post.mention_accounts,
        link_url=post.link_url,
        link_preview_override=post.link_preview_override,
        scheduled_at=post.scheduled_at,
        published_at=post.published_at,
        failed_at=post.failed_at,
        failure_reason=post.failure_reason,
        workflow_id=post.workflow_id,
        workflow_run_id=post.workflow_run_id,
        metadata=post.meta_data,
        music_asset_id=post.music_asset_id,
        pillar_id=post.pillar_id,
        content_brief_id=post.content_brief_id,
        is_recurring=post.is_recurring,
        recurrence_pattern=post.recurrence_pattern,
        recurrence_interval=post.recurrence_interval,
        recurrence_count=post.recurrence_count,
        recurrence_max=post.recurrence_max,
        recurrence_parent_id=post.recurrence_parent_id,
        next_recurrence_at=post.next_recurrence_at,
        created_at=post.created_at,
        updated_at=post.updated_at,
        targets=[
            {
                "social_account_id": str(t.social_account_id),
                "platform": t.social_account.platform,
                "username": t.social_account.username,
                "status": t.status,
                "platform_post_id": t.platform_post_id,
                "platform_url": t.platform_url,
            }
            for t in post.targets
        ],
        comments=[
            {
                "id": str(c.id),
                "author_name": c.author_name,
                "body": c.body,
                "action": c.action,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in post.comments
        ],
    )


# ── /content/media aliases (proxies to media router behaviour) ────────────────
import pathlib  # noqa: E402
import shutil  # noqa: E402

from fastapi import File as FastAPIFile  # noqa: E402
from fastapi import UploadFile  # noqa: E402

from app.core.path_utils import safe_path_component, safe_resolve  # noqa: E402

MEDIA_DIR = pathlib.Path("/app/media")


class MediaItem(BaseModel):
    id: str
    filename: str
    url: str
    media_type: str
    size: int


@router.get("/media")
async def list_content_media(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    media_type: str | None = None,
    current_user: User = Depends(get_current_user),
):
    """Returns uploaded media available for attaching to posts."""
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for f in sorted(MEDIA_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.is_file():
            mt = "image" if f.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp"} else "video"
            if media_type and mt != media_type:
                continue
            items.append({
                "id": f.name,
                "filename": f.name,
                "url": f"/media/files/{f.name}",
                "media_type": mt,
                "size": f.stat().st_size,
            })
    start = (page - 1) * per_page
    return {"items": items[start:start + per_page], "total": len(items), "page": page, "per_page": per_page}


@router.post("/media/upload")
async def upload_content_media(
    file: UploadFile = FastAPIFile(...),
    current_user: User = Depends(get_current_user),
):
    safe_name = f"{uuid.uuid4()}_{safe_path_component(file.filename)}"
    dest = safe_resolve(MEDIA_DIR, safe_name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    return {
        "id": safe_name,
        "filename": safe_name,
        "url": f"/media/files/{safe_name}",
        "size": dest.stat().st_size,
    }


@router.delete("/media/{media_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_content_media(
    media_id: str,
    current_user: User = Depends(get_current_user),
):
    # Only allow a flat, safe filename pattern.
    if not re.fullmatch(r"[a-f0-9]{8}_[A-Za-z0-9_.-]+", media_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid media ID")

    # Construct target path and ensure it stays within MEDIA_DIR.
    target = safe_resolve(MEDIA_DIR, media_id)

    if target.exists() and target.is_file():
        target.unlink()


# ---------------------------------------------------------------------------
# Phase 5 — Approval workflow, Pillars, Content Briefs
# ---------------------------------------------------------------------------


class CommentCreate(BaseModel):
    body: str
    action: str | None = None  # submit_review, approve, reject, comment


class CommentOut(BaseModel):
    id: uuid.UUID
    author_name: str
    body: str
    action: str | None
    created_at: datetime

    class Config:
        from_attributes = True


async def _get_team(user: User, db: AsyncSession) -> Team:
    result = await db.execute(select(Team).join(TeamMember).where(TeamMember.user_id == user.id))
    team = result.scalars().first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return team


@router.post("/posts/{post_id}/submit-review", response_model=PostResponse)
async def submit_for_review(post_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Transition a draft post to REVIEW status."""
    post = await _get_team_post(post_id, current_user, db)
    if post.status != PostStatus.DRAFT:
        raise HTTPException(status_code=400, detail=f"Cannot submit for review from status '{post.status}'")
    post.status = PostStatus.REVIEW
    comment = PostComment(
        post_id=post.id, user_id=current_user.id,
        author_name=current_user.email or "Unknown",
        body="Submitted for review", action="submit_review",
    )
    db.add(comment)
    await log_action(db, user=current_user, action="submit_review", resource_type="post", resource_id=str(post_id))
    await db.commit()
    return await _post_to_response(post, db)


@router.post("/posts/{post_id}/approve", response_model=PostResponse)
async def approve_post(
    post_id: uuid.UUID, comment: CommentCreate | None = None,
    current_user: User = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    """Approve a post in REVIEW status."""
    post = await _get_team_post(post_id, current_user, db)
    if post.status != PostStatus.REVIEW:
        raise HTTPException(status_code=400, detail=f"Cannot approve from status '{post.status}'")
    post.status = PostStatus.APPROVED
    c = PostComment(
        post_id=post.id, user_id=current_user.id,
        author_name=current_user.email or "Unknown",
        body=comment.body if comment else "Approved", action="approve",
    )
    db.add(c)
    await log_action(db, user=current_user, action="approve", resource_type="post", resource_id=str(post_id))
    await db.commit()
    return await _post_to_response(post, db)


@router.post("/posts/{post_id}/reject", response_model=PostResponse)
async def reject_post(
    post_id: uuid.UUID, comment: CommentCreate | None = None,
    current_user: User = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    """Reject a post in REVIEW status — sends it back to DRAFT."""
    post = await _get_team_post(post_id, current_user, db)
    if post.status != PostStatus.REVIEW:
        raise HTTPException(status_code=400, detail=f"Cannot reject from status '{post.status}'")
    post.status = PostStatus.DRAFT
    c = PostComment(
        post_id=post.id, user_id=current_user.id,
        author_name=current_user.email or "Unknown",
        body=comment.body if comment else "Rejected — needs revision", action="reject",
    )
    db.add(c)
    await log_action(db, user=current_user, action="reject", resource_type="post", resource_id=str(post_id))
    await db.commit()
    return await _post_to_response(post, db)


@router.post("/posts/{post_id}/comments", response_model=CommentOut)
async def add_comment(post_id: uuid.UUID, body: CommentCreate, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Add a general comment to a post (preserved through status transitions)."""
    post = await _get_team_post(post_id, current_user, db)
    c = PostComment(
        post_id=post.id, user_id=current_user.id,
        author_name=current_user.email or "Unknown",
        body=body.body, action=body.action or "comment",
    )
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return CommentOut(id=c.id, author_name=c.author_name, body=c.body, action=c.action, created_at=c.created_at)


async def _get_team_post(post_id: uuid.UUID, user: User, db: AsyncSession) -> Post:
    team = await _get_team(user, db)
    result = await db.execute(
        select(Post).where(Post.id == post_id, Post.team_id == team.id)
    )
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return post


# ── Pillars ──────────────────────────────────────────────────────────────────


class PillarCreate(BaseModel):
    name: str
    description: str | None = None
    color: str = "#6366f1"
    sort_order: int = 0


class PillarOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    color: str
    sort_order: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


@router.get("/pillars", response_model=list[PillarOut])
async def list_pillars(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    team = await _get_team(current_user, db)
    result = await db.execute(
        select(Pillar).where(Pillar.team_id == team.id).order_by(Pillar.sort_order, Pillar.name)
    )
    return result.scalars().all()


@router.post("/pillars", response_model=PillarOut, status_code=status.HTTP_201_CREATED)
async def create_pillar(data: PillarCreate, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    team = await _get_team(current_user, db)
    pillar = Pillar(team_id=team.id, **data.model_dump())
    db.add(pillar)
    await db.commit()
    await db.refresh(pillar)
    return pillar


@router.patch("/pillars/{pillar_id}", response_model=PillarOut)
async def update_pillar(pillar_id: uuid.UUID, data: PillarCreate, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    team = await _get_team(current_user, db)
    result = await db.execute(select(Pillar).where(Pillar.id == pillar_id, Pillar.team_id == team.id))
    pillar = result.scalar_one_or_none()
    if not pillar:
        raise HTTPException(status_code=404, detail="Pillar not found")
    for field, value in data.model_dump().items():
        setattr(pillar, field, value)
    await db.commit()
    await db.refresh(pillar)
    return pillar


@router.delete("/pillars/{pillar_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pillar(pillar_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    team = await _get_team(current_user, db)
    result = await db.execute(select(Pillar).where(Pillar.id == pillar_id, Pillar.team_id == team.id))
    pillar = result.scalar_one_or_none()
    if pillar:
        await db.delete(pillar)
        await db.commit()


# ── Content Briefs ───────────────────────────────────────────────────────────


class BriefCreate(BaseModel):
    title: str
    outline: str | None = None
    pillar_id: uuid.UUID | None = None
    target_platforms: list[str] = []
    tone: str | None = None


class BriefOut(BaseModel):
    id: uuid.UUID
    title: str
    outline: str | None
    pillar_id: uuid.UUID | None
    target_platforms: list[str]
    tone: str | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


@router.get("/briefs", response_model=list[BriefOut])
async def list_briefs(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    team = await _get_team(current_user, db)
    result = await db.execute(
        select(ContentBrief).where(ContentBrief.team_id == team.id).order_by(ContentBrief.created_at.desc())
    )
    return result.scalars().all()


@router.post("/briefs", response_model=BriefOut, status_code=status.HTTP_201_CREATED)
async def create_brief(data: BriefCreate, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    team = await _get_team(current_user, db)
    brief = ContentBrief(team_id=team.id, **data.model_dump())
    db.add(brief)
    await db.commit()
    await db.refresh(brief)
    return brief


@router.delete("/briefs/{brief_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_brief(brief_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    team = await _get_team(current_user, db)
    result = await db.execute(select(ContentBrief).where(ContentBrief.id == brief_id, ContentBrief.team_id == team.id))
    brief = result.scalar_one_or_none()
    if brief:
        await db.delete(brief)
        await db.commit()
