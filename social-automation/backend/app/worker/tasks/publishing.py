import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
from celery import shared_task
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.models.content import Post, PostStatus, PostTarget
from app.models.queue import PublishQueue, QueueStatus
from app.models.social_account import SocialAccount
from app.services.db_sync import sync_after_worker_task
from app.services.publishing import publish_to_platform
from app.services.slack_notifications import post_alert_to_slack, post_publishing_to_slack
from app.services.spellcheck import auto_correct
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

def _compute_post_rollup(
    *,
    target_statuses: list[str],
    in_flight: bool,
    failure_reason: str | None,
) -> tuple[PostStatus, bool, str | None]:
    """Compute overall post status from per-target statuses.

    Rules:
    - Any published target means the overall post is successful when work is done.
    - Unsupported/skip targets should not poison overall success.
    - While any targets are still in-flight, keep overall status as PUBLISHING.
    """
    total = len(target_statuses)
    if total == 0:
        return (PostStatus.FAILED, False, "No target accounts assigned to this post")

    published = sum(1 for s in target_statuses if s == "published")
    skipped = sum(1 for s in target_statuses if s == "skipped")
    pending = sum(1 for s in target_statuses if s == "pending")

    if in_flight or pending > 0:
        return (PostStatus.PUBLISHING, False, None)

    # Done: no in-flight work remains.
    if published > 0:
        partial = published != total
        return (PostStatus.PUBLISHED, partial, None)

    # No publish succeeded → overall failure.
    if skipped == total:
        return (PostStatus.FAILED, False, "All targets were skipped (unsupported for publishing)")

    return (PostStatus.FAILED, False, failure_reason or "No targets published successfully")


async def _rollup_post_status(post: Post, db: AsyncSession) -> None:
    """Update post.status/failure_reason based on current PostTarget + queue state."""
    target_result = await db.execute(select(PostTarget).where(PostTarget.post_id == post.id))
    targets = target_result.scalars().all()
    statuses = [t.status for t in targets]

    inflight_result = await db.execute(
        select(PublishQueue).where(
            PublishQueue.post_id == post.id,
            PublishQueue.status.in_([QueueStatus.PENDING, QueueStatus.PROCESSING]),
        ).limit(1)
    )
    in_flight = inflight_result.scalar_one_or_none() is not None

    # Prefer a specific failure reason from the first failed target.
    failure_reason: str | None = None
    for t in targets:
        if t.status == "failed" and t.error_message:
            failure_reason = t.error_message
            break

    new_status, partial, new_failure_reason = _compute_post_rollup(
        target_statuses=statuses,
        in_flight=in_flight,
        failure_reason=failure_reason,
    )

    post.status = new_status
    if new_status == PostStatus.FAILED:
        post.failed_at = datetime.now(UTC)
        post.failure_reason = new_failure_reason
    else:
        post.failed_at = None
        post.failure_reason = None
        if new_status == PostStatus.PUBLISHED and not post.published_at:
            post.published_at = datetime.now(UTC)

    # Store a small summary for the UI (without adding a new PostStatus enum).
    meta = post.meta_data or {}
    meta["publish_summary"] = {
        "total": len(statuses),
        "published": sum(1 for s in statuses if s == "published"),
        "failed": sum(1 for s in statuses if s == "failed"),
        "skipped": sum(1 for s in statuses if s == "skipped"),
        "partial": bool(partial),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    post.meta_data = meta
    flag_modified(post, "meta_data")


async def _notify_publish_failure(
    *,
    post: Post | None,
    account: SocialAccount | None,
    queue_item: PublishQueue | None,
    reason: str,
) -> None:
    """Best-effort Slack alert for final publish failures (never raises)."""
    post_id = str(getattr(post, "id", "") or "unknown")
    platform = getattr(account, "platform", None) or "unknown"
    queue_id = str(getattr(queue_item, "id", "") or "unknown")
    reason_clean = (reason or "").replace("\n", " ").strip()
    text = "\n".join(
        [
            "*We couldn’t publish a post*",
            "• What to do next: open SocialAuto → Posts → Failed, then retry (or reconnect the account if needed).",
            f"• What happened: {reason_clean[:300] or 'Unknown error'}",
            "",
            "*Details*",
            f"• post: `{post_id}`",
            f"• platform: `{platform}`",
            f"• queue item: `{queue_id}`",
            "_Cloudless · Clear skies. Zero friction._",
        ]
    )[:2000]
    await post_alert_to_slack(text)


async def _notify_publish_success(post: Post, account: SocialAccount, platform_url: str | None) -> None:
    """Fire-and-forget webhook + email when a publish succeeds.

    Reads PUBLISH_SUCCESS_WEBHOOK_URL from the environment. If the post has a
    workflow_run_id, it is included in the payload so downstream systems can
    correlate the publish event back to the originating workflow run.
    Also sends a transactional email to the post author if email
    notifications are enabled.
    """
    webhook_url = os.environ.get("PUBLISH_SUCCESS_WEBHOOK_URL", "")
    if webhook_url:
        payload = {
            "event": "publish.success",
            "post_id": str(post.id),
            "platform": account.platform,
            "account_id": str(account.id),
            "platform_url": platform_url,
            "published_at": datetime.now(UTC).isoformat(),
            "workflow_run_id": str(post.workflow_run_id) if post.workflow_run_id else None,
            "workflow_id": str(post.workflow_id) if post.workflow_id else None,
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(webhook_url, json=payload)
                logger.info("Publish success webhook → %s  status=%s", webhook_url, r.status_code)
        except Exception as exc:
            logger.warning("Publish success webhook failed: %s", exc)

    # Send transactional email notification to the post author
    try:
        from app.models.user import User
        from app.services.email_templates import send_post_published_email

        settings = get_settings()
        engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
        async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as db:
            if post.user_id:
                result = await db.execute(select(User).where(User.id == post.user_id))
                author = result.scalar_one_or_none()
                if author:
                    prefs = author.notification_preferences or {}
                    if prefs.get("email_new_post", True):
                        await send_post_published_email(author, post, platform=account.platform)
        await engine.dispose()
    except Exception:
        logger.warning("Failed to send post-published email for post %s", post.id)

    # Slack publish-success notification (best-effort; non-fatal).
    try:
        url = platform_url or "n/a"
        text = "\n".join(
            [
                "*Published*",
                f"• Platform: *{account.platform}*",
                f"• Link: {url}",
                "",
                "*Details*",
                f"• post: `{post.id}`",
                "_Cloudless · Clear skies. Zero friction._",
            ]
        )[:2000]
        await post_publishing_to_slack(text)
    except Exception:
        logger.debug("Slack publish-success notification failed (non-fatal)", exc_info=True)

# Bind shared tasks in this process to the Redis-backed app (not default AMQP).
celery_app.set_default()
celery_app.set_current()


@asynccontextmanager
async def _worker_db():
    """Fresh connection per task invocation — NullPool avoids event-loop conflicts in Celery."""
    engine = create_async_engine(get_settings().DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


# ── helpers ──────────────────────────────────────────────────────────────────

async def _process_publish_queue_async() -> None:
    async with _worker_db() as db:
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
                    await _notify_publish_failure(
                        post=None,
                        account=None,
                        queue_item=item,
                        reason="Post not found for publish queue item",
                    )
                    continue

                account_result = await db.execute(
                    select(SocialAccount).where(SocialAccount.id == item.social_account_id)
                )
                account = account_result.scalar_one_or_none()
                if not account:
                    item.status = QueueStatus.FAILED
                    await db.commit()
                    await _notify_publish_failure(
                        post=post,
                        account=None,
                        queue_item=item,
                        reason="Social account not found for publish queue item",
                    )
                    continue

                if post.content_text:
                    post.content_text = await auto_correct(post.content_text)

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
                    await _notify_publish_success(post, account, pub.platform_url)
                elif getattr(pub, "skipped", False):
                    # Soft-skip: do not retry, do not fail the whole post.
                    item.status = QueueStatus.COMPLETED
                    if target:
                        target.status = "skipped"
                        target.error_message = pub.error
                else:
                    item.attempts += 1
                    if item.attempts >= item.max_attempts:
                        item.status = QueueStatus.FAILED
                        if target:
                            target.status = "failed"
                            target.error_message = pub.error
                        await _notify_publish_failure(
                            post=post,
                            account=account,
                            queue_item=item,
                            reason=pub.error or "unknown publish error",
                        )
                    else:
                        item.status = QueueStatus.PENDING
                        item.locked_at = None
                        item.locked_by = None

                # Ensure rollup queries see the in-memory changes.
                await db.flush()
                await _rollup_post_status(post, db)
                await db.commit()

            except Exception:
                await db.rollback()
                item.attempts += 1
                is_final = item.attempts >= item.max_attempts
                item.status = QueueStatus.FAILED if is_final else QueueStatus.PENDING
                item.locked_at = None
                item.locked_by = None

                # Best-effort: update the target and roll up overall post status.
                err_post: Post | None = None
                err_account: SocialAccount | None = None
                err_target: PostTarget | None = None
                if is_final:
                    try:
                        post_result = await db.execute(select(Post).where(Post.id == item.post_id))
                        err_post = post_result.scalar_one_or_none()
                        acct_result = await db.execute(
                            select(SocialAccount).where(SocialAccount.id == item.social_account_id)
                        )
                        err_account = acct_result.scalar_one_or_none()
                        if err_post and err_account:
                            tgt_result = await db.execute(
                                select(PostTarget).where(
                                    PostTarget.post_id == err_post.id,
                                    PostTarget.social_account_id == err_account.id,
                                )
                            )
                            err_target = tgt_result.scalar_one_or_none()
                            if err_target:
                                err_target.status = "failed"
                                err_target.error_message = "Unhandled exception while publishing (see worker logs)"
                    except Exception:  # noqa: BLE001
                        err_post = None
                        err_account = None
                        err_target = None

                await db.flush()
                if err_post:
                    await _rollup_post_status(err_post, db)
                await db.commit()

                if item.status == QueueStatus.FAILED:
                    await _notify_publish_failure(
                        post=err_post,
                        account=err_account,
                        queue_item=item,
                        reason="Unhandled exception while publishing (see worker logs)",
                    )


async def _check_scheduled_posts_async() -> None:
    async with _worker_db() as db:
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

            # If there are no targets, fail the post immediately instead of
            # setting it to PUBLISHING and leaving it stuck forever.
            if not targets:
                post.status = PostStatus.FAILED
                post.failed_at = now
                post.failure_reason = "No target accounts assigned to this post"
                await db.commit()
                continue

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
    async with _worker_db() as db:
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

        post.status = PostStatus.PUBLISHING
        post.failed_at = None
        post.failure_reason = None
        await db.commit()
        return {"success": True, "results": results}


# ── Celery tasks (sync wrappers) ─────────────────────────────────────────────

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def process_publish_queue(self) -> None:
    asyncio.run(_process_publish_queue_async())
    # Push worker writes (publish_queue, posts, post_targets) to D1 primary
    asyncio.run(sync_after_worker_task(["publish_queue", "posts", "post_targets"]))


@shared_task
def check_scheduled_posts() -> None:
    asyncio.run(_check_scheduled_posts_async())
    # Push worker writes (posts, publish_queue) to D1 primary
    asyncio.run(sync_after_worker_task(["posts", "publish_queue"]))


@shared_task
def publish_post_now(post_id: str, account_ids: list[str]) -> dict:
    result = asyncio.run(_publish_post_now_async(post_id, account_ids))
    # Push worker writes (posts, post_targets, publish_queue) to D1 primary
    asyncio.run(sync_after_worker_task(["posts", "post_targets", "publish_queue"]))
    return result
