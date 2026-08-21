import asyncio
from datetime import datetime, UTC

from celery import shared_task
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session_maker
from app.models.content import Post, PostStatus, PostTarget
from app.models.social_account import SocialAccount
from app.models.queue import PublishQueue, QueueStatus
from app.services.publishing import publish_to_platform


# ── helpers ──────────────────────────────────────────────────────────────────

async def _process_publish_queue_async() -> None:
    async with async_session_maker() as db:
        result = await db.execute(
            select(PublishQueue)
            .where(
                PublishQueue.status == QueueStatus.PENDING,
                PublishQueue.scheduled_at <= datetime.now(UTC),
            )
            .order_by(PublishQueue.priority.desc(), PublishQueue.scheduled_at.asc())
            .limit(50)
        )
        items = result.scalars().all()

        for item in items:
            item.status = QueueStatus.PROCESSING
            item.locked_at = datetime.now(UTC)
            item.locked_by = "celery-worker"
            await db.commit()

            try:
                post_result = await db.execute(select(Post).where(Post.id == item.post_id))
                post = post_result.scalar_one_or_none()
                if not post:
                    item.status = QueueStatus.FAILED
                    await db.commit()
                    continue

                account_result = await db.execute(
                    select(SocialAccount).where(SocialAccount.id == item.social_account_id)
                )
                account = account_result.scalar_one_or_none()
                if not account:
                    item.status = QueueStatus.FAILED
                    await db.commit()
                    continue

                pub = await publish_to_platform(account, post, db)

                target_result = await db.execute(
                    select(PostTarget).where(
                        PostTarget.post_id == post.id,
                        PostTarget.social_account_id == account.id,
                    )
                )
                target = target_result.scalar_one_or_none()

                if pub.success:
                    item.status = QueueStatus.COMPLETED
                    if target:
                        target.status = "published"
                        target.platform_post_id = pub.platform_post_id
                        target.platform_url = pub.platform_url
                        target.published_at = datetime.now(UTC)
                    post.published_at = datetime.now(UTC)
                    post.status = PostStatus.PUBLISHED
                else:
                    item.attempts += 1
                    if item.attempts >= item.max_attempts:
                        item.status = QueueStatus.FAILED
                        if target:
                            target.status = "failed"
                            target.error_message = pub.error
                        post.failed_at = datetime.now(UTC)
                        post.failure_reason = pub.error
                        post.status = PostStatus.FAILED
                    else:
                        item.status = QueueStatus.PENDING
                        item.locked_at = None
                        item.locked_by = None

                await db.commit()

            except Exception as exc:
                item.attempts += 1
                item.status = QueueStatus.FAILED if item.attempts >= item.max_attempts else QueueStatus.PENDING
                item.locked_at = None
                item.locked_by = None
                await db.commit()


async def _check_scheduled_posts_async() -> None:
    async with async_session_maker() as db:
        now = datetime.now(UTC)
        result = await db.execute(
            select(Post).where(
                and_(
                    Post.status == PostStatus.SCHEDULED,
                    Post.scheduled_at <= now,
                )
            )
        )
        posts = result.scalars().all()

        for post in posts:
            post_targets_result = await db.execute(
                select(PostTarget).where(PostTarget.post_id == post.id)
            )
            targets = post_targets_result.scalars().all()

            for target in targets:
                existing = await db.execute(
                    select(PublishQueue).where(
                        PublishQueue.post_id == post.id,
                        PublishQueue.social_account_id == target.social_account_id,
                        PublishQueue.status.in_([QueueStatus.PENDING, QueueStatus.PROCESSING]),
                    )
                )
                if existing.scalar_one_or_none():
                    continue

                db.add(
                    PublishQueue(
                        post_id=post.id,
                        social_account_id=target.social_account_id,
                        scheduled_at=post.scheduled_at or now,
                        priority=5,
                        status=QueueStatus.PENDING,
                    )
                )

            post.status = PostStatus.PUBLISHING
            await db.commit()


async def _publish_post_now_async(post_id: str, account_ids: list[str]) -> dict:
    async with async_session_maker() as db:
        post_result = await db.execute(select(Post).where(Post.id == post_id))
        post = post_result.scalar_one_or_none()
        if not post:
            return {"success": False, "error": "Post not found"}

        results = []
        for account_id in account_ids:
            acct_result = await db.execute(
                select(SocialAccount).where(SocialAccount.id == account_id)
            )
            account = acct_result.scalar_one_or_none()
            if not account:
                results.append({"account_id": account_id, "success": False, "error": "Account not found"})
                continue

            existing = await db.execute(
                select(PublishQueue).where(
                    PublishQueue.post_id == post.id,
                    PublishQueue.social_account_id == account.id,
                    PublishQueue.status.in_([QueueStatus.PENDING, QueueStatus.PROCESSING]),
                )
            )
            if not existing.scalar_one_or_none():
                db.add(
                    PublishQueue(
                        post_id=post.id,
                        social_account_id=account.id,
                        scheduled_at=datetime.now(UTC),
                        priority=10,
                        status=QueueStatus.PENDING,
                    )
                )
            results.append({"account_id": account_id, "queued": True})

        post.status = PostStatus.SCHEDULED
        await db.commit()
        return {"success": True, "results": results}


# ── Celery tasks (sync wrappers) ─────────────────────────────────────────────

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def process_publish_queue(self) -> None:
    asyncio.run(_process_publish_queue_async())


@shared_task
def check_scheduled_posts() -> None:
    asyncio.run(_check_scheduled_posts_async())


@shared_task
def publish_post_now(post_id: str, account_ids: list[str]) -> dict:
    return asyncio.run(_publish_post_now_async(post_id, account_ids))
