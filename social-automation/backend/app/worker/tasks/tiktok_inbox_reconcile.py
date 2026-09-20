"""Reconcile TikTok MEDIA_UPLOAD inbox drafts with their real publish state.

MEDIA_UPLOAD returns success as soon as the draft lands in the creator's
TikTok app inbox (status SEND_TO_USER_INBOX). The post then shows
``published`` in SocialAuto even though nothing is live until the user
finishes the draft in the mobile app — there is no callback or webhook.

This task re-polls ``status/fetch`` on stored publish_ids:

- PUBLISH_COMPLETE  → swap publish_id for the real Display video id and set
  platform_url (analytics sync picks it up from there).
- FAILED / CANCELLED → mark the target failed with TikTok's reason.
- SEND_TO_USER_INBOX older than 24h → flag the target with an actionable
  error_message (serialized to the UI) instead of pretending it is live.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from celery import shared_task
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.security import decrypt_token
from app.models.content import Post, PostTarget
from app.models.social_account import SocialAccount
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()

INBOX_PENDING_AFTER = timedelta(hours=24)
INBOX_PENDING_MESSAGE = (
    "Draft delivered to TikTok app inbox — open TikTok → Inbox → the draft "
    "notification and finish publishing to go live"
)


@asynccontextmanager
async def _worker_db():
    engine = create_async_engine(get_settings().DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


@shared_task
def reconcile_tiktok_inbox() -> dict:
    """Re-poll publish_id targets; upgrade finished drafts, flag stale ones."""
    return asyncio.run(_reconcile_async())


async def _reconcile_async() -> dict:
    from app.services.tiktok_api import TikTokAPIClient, is_tiktok_publish_id

    summary: dict[str, Any] = {"checked": 0, "published": 0, "failed": 0, "pending": 0, "errors": []}
    async with _worker_db() as db:
        q = (
            select(PostTarget)
            .join(SocialAccount, SocialAccount.id == PostTarget.social_account_id)
            .join(Post, Post.id == PostTarget.post_id)
            .where(
                SocialAccount.platform == "tiktok",
                SocialAccount.status == "active",
                PostTarget.status == "published",
                PostTarget.platform_post_id.isnot(None),
            )
            .options(selectinload(PostTarget.post), selectinload(PostTarget.social_account))
        )
        targets = [
            t for t in (await db.execute(q)).scalars().all()
            if is_tiktok_publish_id(t.platform_post_id)
        ]

        clients: dict[str, TikTokAPIClient] = {}
        for t in targets:
            account = t.social_account
            if not account:
                continue
            summary["checked"] += 1
            try:
                client = clients.get(str(account.id))
                if client is None:
                    client = TikTokAPIClient(
                        access_token=decrypt_token(account.access_token_enc),
                        open_id=(account.meta_data or {}).get("open_id"),
                    )
                    clients[str(account.id)] = client

                resp = await client.check_publish_status(t.platform_post_id)
                status_data = resp.get("data") or {}
                status_value = str(status_data.get("status") or "")

                if status_value == "PUBLISH_COMPLETE":
                    ids = status_data.get("publicaly_available_post_id") or []
                    video_id = str(ids[0]) if ids else None
                    if video_id:
                        username = account.username or ""
                        t.platform_post_id = video_id
                        t.platform_url = (
                            f"https://www.tiktok.com/@{username}/video/{video_id}"
                            if username else t.platform_url
                        )
                        t.error_message = None
                        if t.post is not None:
                            ps = dict(t.post.platform_specific or {})
                            tt = dict(ps.get("tiktok") or {})
                            tt["publicaly_available_post_id"] = video_id
                            tt["inbox_status"] = "PUBLISH_COMPLETE"
                            ps["tiktok"] = tt
                            t.post.platform_specific = ps
                        summary["published"] += 1
                elif status_value in ("FAILED", "CANCELLED"):
                    reason = status_data.get("fail_reason") or status_value
                    t.status = "failed"
                    t.error_message = f"TikTok publish {status_value.lower()}: {reason}"
                    summary["failed"] += 1
                elif status_value == "SEND_TO_USER_INBOX":
                    published_at = t.published_at or (
                        t.post.published_at if t.post else None
                    ) or t.created_at
                    age = datetime.now(UTC) - (published_at or datetime.now(UTC))
                    if age > INBOX_PENDING_AFTER and not t.error_message:
                        t.error_message = INBOX_PENDING_MESSAGE
                    summary["pending"] += 1
            except Exception as exc:  # noqa: BLE001
                summary["errors"].append(f"target {t.id}: {exc}")

        await db.commit()
    return summary
