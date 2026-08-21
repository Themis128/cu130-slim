import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.auth import get_current_user
from app.db.session import get_db
from app.models.content import Post, PostStatus, PostTarget
from app.models.queue import PublishQueue, QueueStatus
from app.models.social_account import SocialAccount
from app.models.user import Team, TeamMember, User

router = APIRouter()


class QueueItemResponse(BaseModel):
    id: uuid.UUID
    post_id: uuid.UUID
    social_account_id: uuid.UUID
    scheduled_at: datetime
    priority: int
    attempts: int
    max_attempts: int
    status: QueueStatus
    locked_at: datetime | None
    locked_by: str | None
    created_at: datetime
    post_title: str | None = None
    platform: str | None = None

    class Config:
        from_attributes = True


class QueueListResponse(BaseModel):
    items: list[QueueItemResponse]
    total: int
    page: int
    page_size: int


@router.post("/queue", response_model=QueueItemResponse, status_code=status.HTTP_201_CREATED)
async def add_to_queue(
    post_id: uuid.UUID,
    social_account_id: uuid.UUID,
    scheduled_at: datetime,
    priority: int = 0,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Verify post exists and user has access
    result = await db.execute(
        select(Post)
        .join(Team)
        .join(TeamMember)
        .where(Post.id == post_id, TeamMember.user_id == current_user.id)
    )
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    if post.status not in [PostStatus.SCHEDULED, PostStatus.DRAFT]:
        raise HTTPException(status_code=400, detail="Post must be scheduled or draft")

    # Verify social account
    result = await db.execute(
        select(SocialAccount).where(SocialAccount.id == social_account_id)
    )
    account = result.scalar_one_or_none()
    if not account or account.team_id != post.team_id:
        raise HTTPException(status_code=404, detail="Social account not found")

    # Create queue item
    queue_item = PublishQueue(
        post_id=post_id,
        social_account_id=social_account_id,
        scheduled_at=scheduled_at,
        priority=priority,
        status=QueueStatus.PENDING,
    )
    db.add(queue_item)

    # Update post status if needed
    if post.status == PostStatus.DRAFT:
        post.status = PostStatus.SCHEDULED
        post.scheduled_at = scheduled_at

    await db.commit()
    await db.refresh(queue_item)

    return await _queue_to_response(queue_item, db)


@router.get("/queue", response_model=QueueListResponse)
async def list_queue(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: QueueStatus | None = Query(None, alias="status"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()
    if not team:
        return QueueListResponse(items=[], total=0, page=page, page_size=page_size)

    query = (
        select(PublishQueue)
        .join(Post, PublishQueue.post_id == Post.id)
        .join(SocialAccount, PublishQueue.social_account_id == SocialAccount.id)
        .where(Post.team_id == team.id)
        .options(selectinload(PublishQueue.post), selectinload(PublishQueue.social_account))
    )

    if status_filter:
        query = query.where(PublishQueue.status == status_filter)

    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query) or 0

    query = query.order_by(PublishQueue.scheduled_at.asc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    items = result.scalars().all()

    return QueueListResponse(
        items=[await _queue_to_response(item, db) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.delete("/queue/{queue_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_scheduled(
    queue_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PublishQueue)
        .join(Post)
        .join(Team)
        .join(TeamMember)
        .where(PublishQueue.id == queue_id, TeamMember.user_id == current_user.id)
        .options(selectinload(PublishQueue.post))
    )
    queue_item = result.scalar_one_or_none()
    if not queue_item:
        raise HTTPException(status_code=404, detail="Queue item not found")

    if queue_item.status == QueueStatus.PROCESSING:
        raise HTTPException(status_code=400, detail="Cannot cancel item currently being processed")

    await db.delete(queue_item)
    await db.commit()


@router.post("/retry/{queue_id}", response_model=QueueItemResponse)
async def retry_failed(
    queue_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PublishQueue)
        .join(Post)
        .join(Team)
        .join(TeamMember)
        .where(PublishQueue.id == queue_id, TeamMember.user_id == current_user.id)
        .options(selectinload(PublishQueue.post), selectinload(PublishQueue.social_account))
    )
    queue_item = result.scalar_one_or_none()
    if not queue_item:
        raise HTTPException(status_code=404, detail="Queue item not found")

    if queue_item.status not in [QueueStatus.FAILED, QueueStatus.COMPLETED]:
        raise HTTPException(status_code=400, detail="Can only retry failed or completed items")

    queue_item.status = QueueStatus.PENDING
    queue_item.attempts = 0
    queue_item.locked_at = None
    queue_item.locked_by = None

    await db.commit()
    await db.refresh(queue_item)

    return await _queue_to_response(queue_item, db)


@router.get("/history", response_model=list[dict])
async def publish_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
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
        select(PostTarget)
        .join(Post)
        .join(SocialAccount)
        .where(Post.team_id == team.id, PostTarget.status.in_(["published", "failed"]))
        .options(selectinload(PostTarget.social_account), selectinload(PostTarget.post))
        .order_by(PostTarget.published_at.desc().nullslast())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    targets = result.scalars().all()

    return [
        {
            "post_id": str(t.post_id),
            "post_title": t.post.content_text[:50] if t.post.content_text else "Untitled",
            "platform": t.social_account.platform,
            "status": t.status,
            "platform_url": t.platform_url,
            "published_at": t.published_at.isoformat() if t.published_at else None,
            "error_message": t.error_message,
        }
        for t in targets
    ]


async def _queue_to_response(queue_item: PublishQueue, db: AsyncSession) -> QueueItemResponse:
    await db.refresh(queue_item, ["post", "social_account"])
    return QueueItemResponse(
        id=queue_item.id,
        post_id=queue_item.post_id,
        social_account_id=queue_item.social_account_id,
        scheduled_at=queue_item.scheduled_at,
        priority=queue_item.priority,
        attempts=queue_item.attempts,
        max_attempts=queue_item.max_attempts,
        status=queue_item.status,
        locked_at=queue_item.locked_at,
        locked_by=queue_item.locked_by,
        created_at=queue_item.created_at,
        post_title=queue_item.post.content_text[:50] if queue_item.post.content_text else None,
        platform=queue_item.social_account.platform,
    )


