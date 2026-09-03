"""Celery task — clone and reschedule recurring posts after each successful publish.

Runs every 5 minutes. Looks for published posts that are flagged as recurring
(``is_recurring=True`` with a non-NONE ``recurrence_pattern``) whose
``next_recurrence_at`` is due. Clones the post (content, media, targets) into a
new scheduled post and advances the recurrence counter.

Inspired by Postiz's recurring-post feature: best-performing content is
re-posted on a configurable cadence (daily/weekly/monthly) so the social
calendar stays full without manual copy-paste.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from celery import shared_task
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.models.content import Post, PostStatus, PostTarget, RecurrencePattern
from app.models.queue import PublishQueue, QueueStatus
from app.services.db_sync import sync_after_worker_task
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _worker_db():
    engine = create_async_engine(get_settings().DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


def _next_recurrence_dt(pattern: RecurrencePattern, interval: int, base: datetime) -> datetime:
    """Compute the next recurrence datetime from ``base``."""
    if pattern == RecurrencePattern.DAILY:
        return base + timedelta(days=interval)
    if pattern == RecurrencePattern.WEEKLY:
        return base + timedelta(weeks=interval)
    if pattern == RecurrencePattern.MONTHLY:
        return base + timedelta(days=interval * 30)
    return base  # NONE — shouldn't reach here


async def _process_recurring_posts_async() -> dict:
    summary = {"cloned": 0, "skipped": 0, "errors": []}
    now = datetime.now(UTC)
    async with _worker_db() as db:
        result = await db.execute(
            select(Post).where(
                Post.is_recurring.is_(True),
                Post.recurrence_pattern != RecurrencePattern.NONE,
                Post.status == PostStatus.PUBLISHED,
                Post.next_recurrence_at.is_not(None),
                Post.next_recurrence_at <= now,
            )
        )
        posts = result.scalars().all()

        for parent in posts:
            try:
                # Check recurrence_max (0 = unlimited)
                if parent.recurrence_max > 0 and parent.recurrence_count >= parent.recurrence_max:
                    parent.is_recurring = False
                    parent.next_recurrence_at = None
                    await db.commit()
                    summary["skipped"] += 1
                    continue

                # Clone the post
                clone = Post(
                    team_id=parent.team_id,
                    user_id=parent.user_id,
                    status=PostStatus.SCHEDULED,
                    content_text=parent.content_text,
                    media_ids=list(parent.media_ids or []),
                    platform_specific=dict(parent.platform_specific or {}),
                    hashtags=list(parent.hashtags or []),
                    mention_accounts=list(parent.mention_accounts or []),
                    link_url=parent.link_url,
                    music_asset_id=parent.music_asset_id,
                    pillar_id=parent.pillar_id,
                    content_brief_id=parent.content_brief_id,
                    scheduled_at=now,
                    is_recurring=False,  # the clone is a one-off
                    recurrence_parent_id=parent.id,
                )
                db.add(clone)
                await db.flush()  # get clone.id

                # Clone targets
                targets_result = await db.execute(
                    select(PostTarget).where(PostTarget.post_id == parent.id)
                )
                for target in targets_result.scalars().all():
                    db.add(
                        PostTarget(
                            post_id=clone.id,
                            social_account_id=target.social_account_id,
                            status="pending",
                        )
                    )
                    # Queue for immediate publishing
                    db.add(
                        PublishQueue(
                            post_id=clone.id,
                            social_account_id=target.social_account_id,
                            scheduled_at=now,
                            priority=5,
                            status=QueueStatus.PENDING,
                        )
                    )

                # Advance the parent's recurrence
                parent.recurrence_count += 1
                parent.next_recurrence_at = _next_recurrence_dt(
                    parent.recurrence_pattern, parent.recurrence_interval, now
                )
                await db.commit()
                summary["cloned"] += 1
                logger.info(
                    "Recurring post %s cloned to %s (count=%d, next=%s)",
                    parent.id, clone.id, parent.recurrence_count, parent.next_recurrence_at,
                )
            except Exception as exc:
                logger.error("Failed to clone recurring post %s: %s", parent.id, exc)
                summary["errors"].append(str(parent.id))
                await db.rollback()

    return summary


@shared_task
def process_recurring_posts() -> dict:
    """Clone and reschedule any due recurring posts."""
    result = asyncio.run(_process_recurring_posts_async())
    asyncio.run(sync_after_worker_task(["posts", "post_targets", "publish_queue"]))
    return result
