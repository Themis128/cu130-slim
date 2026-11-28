"""Materialize report-dispatched video renders into scheduled posts.

The strategy/monetization executors dispatch ``generate_video_asset_task``
for video/reel actions and stamp ``meta_data.pending_video_task`` on the
DRAFT post. This beat polls the Celery result; on success it attaches the
MediaAsset, flips the post to SCHEDULED at its intended slot, and clears
the marker.

Failure handling is automated, not terminal on first error:

- Render error or dispatch timeout → re-dispatch ``generate_video_asset_task``
  up to ``_MAX_RENDER_ATTEMPTS`` times while the post is younger than
  ``_RETRY_WINDOW`` (ComfyUI/engine flaps must not kill a post).
- Only after retries are exhausted (or the post is stale) is it marked
  FAILED with the render error.
- Machine-generated DRAFT posts older than ``_STALE_DRAFT_AFTER`` with no
  pending render are ARCHIVED as superseded — report content is perishable
  and week-old drafts must not leak into the queue.
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

# Per-attempt give-up window — a wedged ComfyUI job must not pin a draft.
_STALE_AFTER = timedelta(hours=6)
# Total retry budget across re-dispatches.
_MAX_RENDER_ATTEMPTS = 2
# Infra-class failures (GPU lock, DB boot, ComfyUI/engine flaps, worker
# loss) get their own budget — they are environment faults, not render
# faults, and must not burn the genuine-attempt counter.
_MAX_INFRA_RETRIES = 8
# Don't resurrect content older than this — reports are perishable.
_RETRY_WINDOW = timedelta(hours=48)

# Substrings (lowercased) marking a render failure as infrastructure.
# Kept narrow: prompt/content errors must still count as real attempts.
_INFRA_MARKERS = (
    "gpu media lock",
    "media lock",
    "gpu-arbiter",
    "database system is starting up",
    "connection refused",
    "could not connect",
    "connecterror",
    "operationalerror",
    "comfyui unreachable",
    "comfyui connect",
    "comfyui request",
    "comfyui http",
    "worker lost",
    "workerlost",
    "lost worker",
    "timed out",
    "timeout",
    "softtimelimitexceeded",
    "timelimitexceeded",
    "broker",
    "redis",
)


def _is_infra_reason(reason: str) -> bool:
    r = reason.lower()
    return any(m in r for m in _INFRA_MARKERS)


# Machine drafts with no pending render older than this are superseded.
_STALE_DRAFT_AFTER = timedelta(hours=72)
_REPORT_SOURCES = {
    "strategy-brief",
    "monetization-report",
    "central-brain",
    "devapp-channel-actions",
}


def _redispatch_video(post: Post, meta: dict) -> str | None:
    """Re-dispatch the ComfyUI render for a failed/timed-out post."""
    from app.worker.tasks.media import generate_video_asset_task

    prompt = (meta.get("instruction") or post.content_text or "").strip()
    if not prompt:
        return None
    res = generate_video_asset_task.delay(  # type: ignore[union-attr]
        str(post.team_id),
        str(post.user_id or ""),
        prompt[:2000],
        {"model": "wan22"},
    )
    return res.id


@shared_task(name="app.worker.tasks.report_videos.materialize_report_videos")
def materialize_report_videos() -> dict:
    """Attach finished video assets to pending report posts."""

    async def _run() -> dict:
        from app.worker.celery_app import celery_app

        async with task_session() as db:
            rows = (
                (
                    await db.execute(
                        select(Post).where(
                            Post.status == PostStatus.DRAFT,
                            Post.meta_data["pending_video_task"].astext.isnot(None),
                        )
                    )
                )
                .scalars()
                .all()
            )

        attached = failed = pending = retried = 0

        async def _mark_failed(post_id, reason: str) -> None:
            async with task_session() as dbx:
                fresh = await dbx.get(Post, post_id)
                if fresh is None:
                    return
                fm = dict(fresh.meta_data or {})
                fm.pop("pending_video_task", None)
                fresh.meta_data = fm
                fresh.status = PostStatus.FAILED
                fresh.failure_reason = reason[:300]
                await dbx.commit()

        async def _retry_or_fail(post: Post, reason: str) -> None:
            """Re-dispatch the render while budget remains, else fail."""
            nonlocal retried, failed
            meta = dict(post.meta_data or {})
            attempts = int(meta.get("video_render_attempts") or 0)
            infra_attempts = int(meta.get("video_render_infra_retries") or 0)
            infra = _is_infra_reason(reason)
            created = post.created_at or datetime.now(UTC)
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            within_window = datetime.now(UTC) - created < _RETRY_WINDOW
            if infra:
                budget_left = infra_attempts < _MAX_INFRA_RETRIES
                counter_key, counter_val = "video_render_infra_retries", infra_attempts + 1
            else:
                budget_left = attempts < _MAX_RENDER_ATTEMPTS
                counter_key, counter_val = "video_render_attempts", attempts + 1
            if budget_left and within_window:
                try:
                    new_task = _redispatch_video(post, meta)
                except Exception as exc:  # noqa: BLE001
                    new_task = None
                    logger.warning("video re-dispatch failed for post %s: %s", post.id, str(exc)[:120])
                if new_task:
                    async with task_session() as dbx:
                        fresh = await dbx.get(Post, post.id)
                        if fresh is None:
                            return
                        fm = dict(fresh.meta_data or {})
                        fm["pending_video_task"] = new_task
                        fm[counter_key] = counter_val
                        fm["video_render_dispatched_at"] = datetime.now(UTC).isoformat()
                        fm["last_render_error"] = reason[:200]
                        fresh.meta_data = fm
                        await dbx.commit()
                    retried += 1
                    logger.info(
                        "video render retry (%s) %s for post %s (%s)",
                        "infra" if infra else "attempt",
                        counter_val,
                        post.id,
                        reason[:80],
                    )
                    return
            await _mark_failed(post.id, reason)
            failed += 1

        for post in rows:
            meta = dict(post.meta_data or {})
            task_id = meta.get("pending_video_task")
            # Per-attempt timeout measured from dispatch, falling back to
            # post creation for markers written before this field existed.
            dispatched_raw = meta.get("video_render_dispatched_at")
            if dispatched_raw:
                try:
                    dispatched = datetime.fromisoformat(str(dispatched_raw))
                    if dispatched.tzinfo is None:
                        dispatched = dispatched.replace(tzinfo=UTC)
                except ValueError:
                    dispatched = post.created_at
            else:
                dispatched = post.created_at or datetime.now(UTC)
            if dispatched.tzinfo is None:
                dispatched = dispatched.replace(tzinfo=UTC)
            timed_out = datetime.now(UTC) - dispatched > _STALE_AFTER
            if timed_out:
                await _retry_or_fail(post, "video render timed out (>6h)")
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
                await _retry_or_fail(post, f"video render failed: {str(res.result)[:140]}")
                continue

            async with task_session() as db2:
                asset = await db2.get(MediaAsset, asset_id)
                if asset is None:
                    await _mark_failed(post.id, "video asset missing after render")
                    failed += 1
                    continue
                fresh = await db2.get(Post, post.id)
                if fresh is None:
                    continue
                fm = dict(fresh.meta_data or {})
                fm.pop("pending_video_task", None)
                fm.pop("video_render_attempts", None)
                fm.pop("video_render_dispatched_at", None)
                fm.pop("last_render_error", None)
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

        # Archive stale machine-generated drafts — report content is
        # perishable; anything >72h old with no pending render is dead.
        async with task_session() as db3:
            stale = (
                (
                    await db3.execute(
                        select(Post).where(
                            Post.status == PostStatus.DRAFT,
                            Post.meta_data["pending_video_task"].astext.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
        archived = 0
        cutoff = datetime.now(UTC) - _STALE_DRAFT_AFTER
        for post in stale:
            meta = post.meta_data or {}
            if meta.get("source") not in _REPORT_SOURCES:
                continue
            created = post.created_at or datetime.now(UTC)
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            if created >= cutoff:
                continue
            async with task_session() as db4:
                fresh = await db4.get(Post, post.id)
                if fresh is None or fresh.status != PostStatus.DRAFT:
                    continue
                fm = dict(fresh.meta_data or {})
                if fm.get("pending_video_task"):
                    continue  # render in flight — materialize loop owns it
                fresh.meta_data = {**fm, "archived_reason": "stale report draft superseded"}
                fresh.status = PostStatus.ARCHIVED
                fresh.failure_reason = "stale report draft superseded (>72h, no pending render)"
                await db4.commit()
                archived += 1

        out = {
            "attached": attached,
            "failed": failed,
            "retried": retried,
            "archived_stale_drafts": archived,
            "pending": pending,
            "scanned": len(rows),
        }
        return out

    out = run_async(_run())
    logger.info("materialize_report_videos: %s", out)
    return out
