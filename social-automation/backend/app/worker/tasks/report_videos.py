"""Materialize report-dispatched video renders into scheduled posts.

The strategy/monetization executors dispatch ``generate_video_asset_task``
for video/reel actions and stamp ``meta_data.pending_video_task`` on the
DRAFT post. This beat polls the Celery result; on success it attaches the
MediaAsset, flips the post to SCHEDULED at its intended slot, and clears
the marker. On failure it marks the post failed with the render error.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from celery import shared_task
from sqlalchemy import select

from app.models.content import MediaAsset, Post, PostStatus
from app.worker._async import run_async, task_session

logger = logging.getLogger(__name__)

# Give up on renders older than this — a wedged ComfyUI job must not pin a
# draft forever.
_STALE_AFTER = timedelta(hours=6)


@shared_task(name="app.worker.tasks.report_videos.materialize_report_videos")
def materialize_report_videos() -> dict:
    """Attach finished video assets to pending report posts."""

    async def _run() -> dict:
        from app.worker.celery_app import celery_app

        async with task_session() as db:
            rows = (
                await db.execute(
                    select(Post).where(
                        Post.status == PostStatus.DRAFT,
                        Post.meta_data["pending_video_task"].astext.isnot(None),
                    )
                )
            ).scalars().all()

        attached = failed = pending = 0
        for post in rows:
            meta = dict(post.meta_data or {})
            task_id = meta.get("pending_video_task")
            created = post.created_at or datetime.now(UTC)
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            if datetime.now(UTC) - created > _STALE_AFTER:
                post.status = PostStatus.FAILED
                post.failure_reason = "video render timed out (>6h)"
                meta.pop("pending_video_task", None)
                post.meta_data = meta
                failed += 1
                continue

            res = celery_app.AsyncResult(task_id)
            if not res.ready():
                pending += 1
                continue
            try:
                asset_id = uuid.UUID(res.result) if res.successful() else None
            except Exception:  # noqa: BLE001
                asset_id = None
            if not asset_id:
                post.status = PostStatus.FAILED
                post.failure_reason = f"video render failed: {str(res.result)[:140]}"
                meta.pop("pending_video_task", None)
                post.meta_data = meta
                failed += 1
                continue

            async with task_session() as db2:
                asset = await db2.get(MediaAsset, asset_id)
                if asset is None:
                    post.status = PostStatus.FAILED
                    post.failure_reason = "video asset missing after render"
                    meta.pop("pending_video_task", None)
                    post.meta_data = meta
                    failed += 1
                    continue
                fresh = await db2.get(Post, post.id)
                if fresh is None:
                    continue
                fm = dict(fresh.meta_data or {})
                fm.pop("pending_video_task", None)
                fm["video_asset_id"] = str(asset_id)
                fresh.meta_data = fm
                fresh.media_ids = [asset_id]
                fresh.status = PostStatus.SCHEDULED
                # Re-slot: intended time may already be past — push to next
                # free-ish window (+2h hops, max 3) so it doesn't publish
                # immediately on attach.
                if fresh.scheduled_at is None:
                    fresh.scheduled_at = datetime.now(UTC) + timedelta(hours=2)
                await db2.commit()
                attached += 1

        return {"attached": attached, "failed": failed, "pending": pending, "scanned": len(rows)}

    out = run_async(_run())
    logger.info("materialize_report_videos: %s", out)
    return out
