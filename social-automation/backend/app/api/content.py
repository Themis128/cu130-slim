import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.auth import get_current_user
from app.db.session import get_db
from app.models.content import Post, PostStatus, PostTarget
from app.models.user import Team, TeamMember, User

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
    created_at: datetime
    updated_at: datetime
    targets: list[dict] = []

    class Config:
        from_attributes = True


class PostListResponse(BaseModel):
    posts: list[PostResponse]
    total: int
    page: int
    page_size: int


@router.post("/posts", response_model=PostResponse, status_code=status.HTTP_201_CREATED)
async def create_post(post_data: PostCreate, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    # Get user's team (first one for now)
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()
    if not team:
        raise HTTPException(status_code=400, detail="No team found")

    post = Post(
        team_id=team.id,
        user_id=current_user.id,
        status=PostStatus.DRAFT if not post_data.scheduled_at else PostStatus.SCHEDULED,
        content_text=post_data.content_text,
        media_ids=post_data.media_ids,
        platform_specific=post_data.platform_specific,
        hashtags=post_data.hashtags,
        mention_accounts=post_data.mention_accounts,
        link_url=post_data.link_url,
        link_preview_override=post_data.link_preview_override,
        scheduled_at=post_data.scheduled_at,
        meta_data=post_data.metadata,
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
            Post.status.in_([PostStatus.SCHEDULED, PostStatus.PUBLISHED]),
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
        }
        for p in posts
    ]


@router.get("/posts/{post_id}", response_model=PostResponse)
async def get_post(post_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Post)
        .options(selectinload(Post.targets).selectinload(PostTarget.social_account))
        .where(Post.id == post_id)
    )
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    return await _post_to_response(post, db)


@router.patch("/posts/{post_id}", response_model=PostResponse)
async def update_post(post_id: uuid.UUID, post_data: PostUpdate, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Post).where(Post.id == post_id).options(selectinload(Post.targets))
    )
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    if post.status not in [PostStatus.DRAFT, PostStatus.SCHEDULED]:
        raise HTTPException(status_code=400, detail="Cannot edit published/failed post")

    update_data = post_data.model_dump(exclude_unset=True)
    target_account_ids = update_data.pop("target_account_ids", None)

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
async def delete_post(post_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Post).where(Post.id == post_id))
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    if post.status in [PostStatus.PUBLISHED, PostStatus.PUBLISHING]:
        raise HTTPException(status_code=400, detail="Cannot delete published post")

    await db.delete(post)
    await db.commit()


@router.post("/posts/{post_id}/schedule", response_model=PostResponse)
async def schedule_post(post_id: uuid.UUID, scheduled_at: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    # Parse ISO 8601 string, support Z suffix
    try:
        if scheduled_at.endswith('Z'):
            scheduled_at = scheduled_at[:-1] + '+00:00'
        dt = datetime.fromisoformat(scheduled_at)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid datetime format")

    result = await db.execute(select(Post).where(Post.id == post_id))
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    post.status = PostStatus.SCHEDULED
    post.scheduled_at = dt
    await db.commit()
    await db.refresh(post)

    return await _post_to_response(post, db)


@router.post("/posts/{post_id}/publish-now", response_model=PostResponse)
async def publish_now(post_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from app.worker.tasks.publishing import process_publish_queue, publish_post_now

    result = await db.execute(select(Post).where(Post.id == post_id).options(selectinload(Post.targets)))
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    post.status = PostStatus.SCHEDULED
    post.scheduled_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(post)

    account_ids = [str(t.social_account_id) for t in post.targets]
    if account_ids:
        import asyncio
        loop = asyncio.get_event_loop()
        pid_str = str(post_id)
        loop.run_in_executor(None, lambda: publish_post_now.delay(pid_str, account_ids))
        loop.run_in_executor(None, lambda: process_publish_queue.apply_async(countdown=2))

    return await _post_to_response(post, db)


@router.post("/posts/{post_id}/duplicate", response_model=PostResponse)
async def duplicate_post(post_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Post).where(Post.id == post_id).options(selectinload(Post.targets)))
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
    )
    db.add(new_post)
    await db.flush()

    for target in post.targets:
        db.add(PostTarget(post_id=new_post.id, social_account_id=target.social_account_id))

    await db.commit()
    await db.refresh(new_post)

    return await _post_to_response(new_post, db)


async def _post_to_response(post: Post, db: AsyncSession) -> PostResponse:
    await db.refresh(post, ["targets"])
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
    )


# ── /content/media aliases (proxies to media router behaviour) ────────────────
import pathlib
import shutil

from fastapi import File as FastAPIFile
from fastapi import UploadFile

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
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = f"{uuid.uuid4()}_{file.filename}"
    dest = MEDIA_DIR / safe_name
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    return {
        "id": safe_name,
        "filename": file.filename,
        "url": f"/media/files/{safe_name}",
        "size": dest.stat().st_size,
    }


@router.delete("/media/{media_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_content_media(
    media_id: str,
    current_user: User = Depends(get_current_user),
):
    # Prevent path traversal attacks
    if ".." in media_id or media_id.startswith("/") or "\\" in media_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid media ID")

    # Construct target path and ensure it stays within MEDIA_DIR
    target = (MEDIA_DIR / media_id).resolve()
    if not target.is_relative_to(MEDIA_DIR.resolve()):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid media ID")

    if target.exists() and target.is_file():
        target.unlink()
