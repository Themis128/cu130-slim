"""Resurrection sweep — revive posts/targets whose failure cause was infra, not content.

Complements ``publish_retry`` (15min, failed queue rows ≤72h, skips TERMINAL)
and ``materialize_report_videos`` (20min, render retry budget 48h/2 attempts):

- Orphaned/auth-class failed targets: queue rows are GC'd after ~3d so
  publish_retry can never see them again, and TERMINAL-classified auth
  errors (401/session/token/login) are skipped forever even after ops
  repairs the credential. This sweep re-arms them at a slow cadence —
  target must sit failed ≥20h, ≤7d old, ≤4 lifetime queue rows.
- Posts FAILED at the video-render stage older than materialize's window:
  re-dispatch ``generate_video_asset_task`` and re-arm
  ``meta_data.pending_video_task`` so materialize_report_videos owns the
  post again (DRAFT + pending task → attach → SCHEDULED → publish).
  Capped at 2 resurrections per post, 5-day age cap.
- Orphaned video posts (PUBLISHING/SUPPRESSED) — render-intent posts whose
  render died and which then drifted past every watcher: no ``media_ids``,
  no ``pending_video_task``, but render evidence in meta
  (``last_render_error`` or ``video_render_dispatched_at``). A publish
  re-queue never regenerates media — the only correct lane is
  DRAFT + fresh pending task (2026-11-24 incident: 6 video posts sat
  media-less through repeated engine flaps).

Content-terminal failures (duplicate, prompt leak, quality gate, mangled
copy) are NEVER resurrected — deterministic, needs a human.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from celery import shared_task
from sqlalchemy import and_, func, or_, select

from app.models.content import Post, PostStatus, PostTarget
from app.models.queue import PublishQueue, QueueStatus
from app.services.error_taxonomy import TERMINAL, classify_publish_error
from app.worker._async import run_async, task_session

logger = logging.getLogger(__name__)

_TARGET_MIN_AGE = timedelta(hours=20)  # cadence between re-arms
_TARGET_MAX_AGE = timedelta(days=7)
_MAX_QUEUE_ROWS = 4  # lifetime publish attempts per (post, account)
_POST_MAX_AGE = timedelta(days=5)
_MAX_RESURRECTIONS = 2

# Auth/credential failure signatures — the class ops can repair
# out-of-band (console re-auth, sidecar restart, token refresh). Everything
# else TERMINAL stays dead.
_AUTH_MARKERS = (
    "401",
    "403",
    "unauthorized",
    "forbidden",
    "session has been invalidated",
    "error validating access token",
    "invalid_grant",
    "invalid_token",
    "invalid refresh token",
    "token has been revoked",
    "expiredtoken",
    "login failed",
    "authenticationrequired",
    "authentication required",
    "credential",
    "reauth",
)

_CONTENT_TERMINAL_MARKERS = (
    "duplicate",
    "identical content",
    "prompt leak",
    "quality gate",
    "seo score",
    "mangled",
    "empty content",
    "semantic",
    "preflight",
    "only photo or video",
    "invalid media",
    "unsupported media",
    "does not accept",
    "url_ownership",
    "domain not verified",
)


def _is_auth_failure(err: str) -> bool:
    low = (err or "").lower()
    if not low:
        return False
    if any(m in low for m in _CONTENT_TERMINAL_MARKERS):
        return False
    return any(m in low for m in _AUTH_MARKERS)


async def _sweep() -> dict:
    now = datetime.now(UTC)
    targets_revived = posts_revived = 0
    async with task_session() as db:
        # --- Part 1: orphaned / auth-class failed targets -----------------
        failed_targets = (
            (
                await db.execute(
                    select(PostTarget).where(
                        PostTarget.status == "failed",
                        PostTarget.updated_at < now - _TARGET_MIN_AGE,
                        PostTarget.updated_at > now - _TARGET_MAX_AGE,
                    )
                )
            )
            .scalars()
            .all()
        )
        for tgt in failed_targets:
            err = tgt.error_message or ""
            eclass = tgt.error_class or classify_publish_error(err)
            if eclass != TERMINAL and not _is_auth_failure(err):
                continue  # transient/unknown → publish_retry owns it
            if eclass == TERMINAL and not _is_auth_failure(err):
                continue  # content-terminal → stays dead
            active = (
                await db.execute(
                    select(func.count())
                    .select_from(PublishQueue)
                    .where(
                        PublishQueue.post_id == tgt.post_id,
                        PublishQueue.social_account_id == tgt.social_account_id,
                        PublishQueue.status.in_([QueueStatus.PENDING, QueueStatus.PROCESSING]),
                    )
                )
            ).scalar_one()
            if active:
                continue
            rows = (
                await db.execute(
                    select(func.count())
                    .select_from(PublishQueue)
                    .where(
                        PublishQueue.post_id == tgt.post_id,
                        PublishQueue.social_account_id == tgt.social_account_id,
                    )
                )
            ).scalar_one()
            if rows >= _MAX_QUEUE_ROWS:
                continue
            post = await db.get(Post, tgt.post_id)
            if post is None or post.status in (PostStatus.ARCHIVED,):
                continue
            db.add(
                PublishQueue(
                    post_id=tgt.post_id,
                    social_account_id=tgt.social_account_id,
                    scheduled_at=now,
                    priority=10,
                    attempts=0,
                    max_attempts=3,
                    status=QueueStatus.PENDING,
                )
            )
            tgt.status = "pending"
            tgt.error_message = None
            targets_revived += 1

        # --- Part 2: failed video-render posts -----------------------------
        failed_posts = (
            (
                await db.execute(
                    select(Post).where(
                        Post.status == PostStatus.FAILED,
                        Post.created_at < now - timedelta(hours=48),
                        Post.created_at > now - _POST_MAX_AGE,
                    )
                )
            )
            .scalars()
            .all()
        )
        for post in failed_posts:
            reason = (post.failure_reason or "").lower()
            if "video render" not in reason and "render" not in reason:
                continue
            posts_revived += _revive_video_post(post, now)

        # --- Part 3: orphaned video posts ----------------------------------
        # Render-intent posts that drifted into PUBLISHING/SUPPRESSED with no
        # media and no armed render — e.g. a manual publish re-queue, or a
        # media-gate suppression landing after the render died. Only the
        # DRAFT+pending_task lane regenerates their media.
        orphaned = (
            (
                await db.execute(
                    select(Post).where(
                        Post.status.in_([PostStatus.PUBLISHING, PostStatus.SUPPRESSED]),
                        func.cardinality(Post.media_ids) == 0,
                        Post.meta_data["pending_video_task"].astext.is_(None),
                        Post.meta_data["last_render_error"].astext.isnot(None),
                        Post.created_at > now - _POST_MAX_AGE,
                    )
                )
            )
            .scalars()
            .all()
        )
        for post in orphaned:
            posts_revived += _revive_video_post(post, now)

        # --- Part 4: pending targets orphaned on dead posts ----------------
        # The publish sweep only claims SCHEDULED posts, so a target left
        # 'pending' on a terminally-dead post is never consumed (observed
        # 2026-11-24: bf87a26f's IG target sat pending while its post was
        # FAILED). FAILED posts younger than the resurrect window keep
        # their targets — Part 2 may still revive them.
        dead_post_ids = (
            (
                await db.execute(
                    select(Post.id).where(
                        or_(
                            Post.status == PostStatus.ARCHIVED,
                            and_(
                                Post.status == PostStatus.FAILED,
                                Post.created_at < now - _POST_MAX_AGE,
                            ),
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
        targets_skipped = 0
        if dead_post_ids:
            orphan_targets = (
                (
                    await db.execute(
                        select(PostTarget).where(
                            PostTarget.status == "pending",
                            PostTarget.post_id.in_(dead_post_ids),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for tgt in orphan_targets:
                tgt.status = "skipped"
                tgt.error_class = "orphaned_target"
                tgt.error_message = "post terminally dead — orphaned target swept"
            targets_skipped = len(orphan_targets)

        await db.commit()

    out = {
        "targets_revived": targets_revived,
        "posts_revived": posts_revived,
        "targets_skipped": targets_skipped,
    }
    if targets_revived or posts_revived or targets_skipped:
        try:
            from app.services.slack_notifications import post_alert_to_slack

            await post_alert_to_slack(
                ":recycle: publish-resurrect: re-armed "
                f"{targets_revived} failed target(s), {posts_revived} failed video post(s), "
                f"skipped {targets_skipped} orphaned target(s) "
                "(infra-class failures only — content-terminal stays dead)"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("resurrect alert failed: %s", exc)
    return out


def _revive_video_post(post: Post, now: datetime) -> int:
    """Re-arm a render-intent post: fresh generate_video_asset_task +
    pending_video_task + DRAFT so materialize_report_videos owns it.
    Returns 1 on dispatch, 0 on skip/failure."""
    meta = dict(post.meta_data or {})
    if meta.get("pending_video_task"):
        return 0  # already re-armed / in flight
    if int(meta.get("resurrect_count") or 0) >= _MAX_RESURRECTIONS:
        return 0
    prompt = (meta.get("instruction") or post.content_text or "").strip()
    if not prompt:
        return 0
    from app.worker.tasks.media import generate_video_asset_task

    try:
        res = generate_video_asset_task.delay(
            str(post.team_id),
            str(post.user_id or ""),
            prompt[:2000],
            {"model": "wan22"},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("resurrect dispatch failed for post %s: %s", post.id, exc)
        return 0
    meta["pending_video_task"] = res.id
    meta["video_render_dispatched_at"] = now.isoformat()
    meta["video_render_attempts"] = 0
    meta["resurrect_count"] = int(meta.get("resurrect_count") or 0) + 1
    post.meta_data = meta
    post.status = PostStatus.DRAFT
    post.failure_reason = None
    logger.info("resurrected video post %s (attempt %s)", post.id, meta["resurrect_count"])
    return 1


@shared_task(name="app.worker.tasks.publish_resurrect.run_publish_resurrect_sweep")
def run_publish_resurrect_sweep() -> dict:
    return run_async(_sweep())
