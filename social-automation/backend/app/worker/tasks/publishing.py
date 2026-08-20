import httpx
from celery import shared_task
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, and_
from datetime import datetime, UTC
import json

from app.core.config import settings
from app.models.content import Post, PostStatus, PostTarget
from app.models.social_account import SocialAccount
from app.models.queue import PublishQueue, QueueStatus
from app.services.publishing import publish_to_platform


engine = create_async_engine(settings.DATABASE_URL, echo=False)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
async def process_publish_queue(self):
    """Process pending items in the publish queue"""
    async with async_session() as db:
        # Get pending queue items
        result = await db.execute(
            select(PublishQueue)
            .where(PublishQueue.status == QueueStatus.PENDING)
            .order_by(PublishQueue.priority.desc(), PublishQueue.created_at.asc())
            .limit(50)
        )
        queue_items = result.scalars().all()

        for item in queue_items:
            try:
                item.status = QueueStatus.PROCESSING
                item.started_at = datetime.now(UTC)
                await db.commit()

                # Get the post
                post_result = await db.execute(select(Post).where(Post.id == item.post_id))
                post = post_result.scalar_one_or_none()
                if not post:
                    item.status = QueueStatus.FAILED
                    item.error_message = "Post not found"
                    await db.commit()
                    continue

                # Get the social account
                account_result = await db.execute(
                    select(SocialAccount).where(SocialAccount.id == item.account_id)
                )
                account = account_result.scalar_one_or_none()
                if not account:
                    item.status = QueueStatus.FAILED
                    item.error_message = "Social account not found"
                    await db.commit()
                    continue

                # Publish to platform
                result = await publish_to_platform(account, post, db)

                if result.success:
                    item.status = QueueStatus.COMPLETED
                    item.completed_at = datetime.now(UTC)
                    item.external_id = result.external_id
                    item.external_url = result.external_url
                    
                    # Update post status
                    post.status = PostStatus.PUBLISHED
                    post.published_at = datetime.now(UTC)
                    post.external_id = result.external_id
                    post.external_url = result.external_url
                else:
                    item.status = QueueStatus.FAILED
                    item.error_message = result.error
                    item.retry_count += 1
                    
                    if item.retry_count < item.max_retries:
                        item.status = QueueStatus.PENDING
                        item.next_retry_at = datetime.now(UTC)
                    
                    # Update post status if all targets failed
                    post.status = PostStatus.FAILED

                await db.commit()

            except Exception as e:
                item.status = QueueStatus.FAILED
                item.error_message = str(e)
                item.retry_count += 1
                await db.commit()


@shared_task
async def check_scheduled_posts():
    """Check for posts that are scheduled to be published now"""
    async with async_session() as db:
        now = datetime.now(UTC)
        
        # Find scheduled posts that are due
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
            # Add to publish queue for each target
            for target in post.targets:
                queue_item = PublishQueue(
                    post_id=post.id,
                    account_id=target.account_id,
                    platform=target.platform,
                    status=QueueStatus.PENDING,
                    priority=5,
                )
                db.add(queue_item)
            
            post.status = PostStatus.QUEUED
            await db.commit()


@shared_task
async def publish_post_now(post_id: str, account_ids: list[str]):
    """Immediately publish a post to specified accounts"""
    async with async_session() as db:
        post_result = await db.execute(select(Post).where(Post.id == post_id))
        post = post_result.scalar_one_or_none()
        if not post:
            return {"success": False, "error": "Post not found"}

        results = []
        for account_id in account_ids:
            account_result = await db.execute(
                select(SocialAccount).where(SocialAccount.id == account_id)
            )
            account = account_result.scalar_one_or_none()
            if not account:
                results.append({"account_id": account_id, "success": False, "error": "Account not found"})
                continue

            queue_item = PublishQueue(
                post_id=post.id,
                account_id=account.id,
                platform=account.platform,
                status=QueueStatus.PENDING,
                priority=10,
            )
            db.add(queue_item)
            results.append({"account_id": account_id, "success": True, "queued": True})

        post.status = PostStatus.QUEUED
        await db.commit()
        return {"success": True, "results": results}