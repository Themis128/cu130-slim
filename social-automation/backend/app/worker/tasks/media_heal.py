"""Media-heal sweep — regenerate branded media for posts whose targets
failed or were skipped on media grounds, then requeue them through the
normal publish path.

Covers the gap ``publish_retry`` cannot: media semantic-QA and
unresolved-media skips classify TERMINAL (retrying the same bytes fails
identically), and the post lands SUPPRESSED with zero published targets.
A freshly composed brand slide built from the post's own copy passes the
gate — verified live 2026-11-10 (8 posts republished after manual heal).

Bounded by design:
- ``media_heal_attempts`` in ``post.meta_data`` caps regeneration at
  ``_MAX_HEAL_ATTEMPTS`` per post; past that the post alerts Slack once
  (``media_heal_alerted``) instead of looping forever.
- Only posts with ZERO published targets are touched — a partially
  published post keeps its existing targets untouched.
- Dedup skips ("identical media", "duplicate content") and auth/terminal
  errors never match the media patterns, so they are never resurrected.
"""

from __future__ import annotations

import io
import logging
from datetime import UTC, datetime

from celery import shared_task
from sqlalchemy import select

from app.models.content import Post, PostStatus, PostTarget
from app.services.carousel_pipeline import (
    _cf_generate_background,
    _qa_generated_background,
    compose_branded_slide,
)
from app.services.cf_models import CF_TXT2IMG_FREE
from app.services.media_storage import persist_generated_image
from app.services.slack_notifications import post_alert_to_slack
from app.worker._async import run_async, task_session
from app.worker.tasks.publishing import publish_post_now

logger = logging.getLogger(__name__)

_MAX_HEAL_ATTEMPTS = 2
_MAX_POSTS_PER_RUN = 10

# Error substrings (lowercased) that mean "the media was the problem" —
# retrying identical bytes cannot help, but new branded media can.
_MEDIA_ERROR_MARKERS = (
    "media semantic qa",
    "media incomplete",
    "could be resolved from storage",
    "missing media",
)
# Explicit exclusions — dedup guards are working as intended.
_EXCLUDED_MARKERS = (
    "identical media",
    "duplicate content",
)


def _is_media_failure(error: str | None) -> bool:
    msg = (error or "").lower()
    if not msg:
        return False
    if any(marker in msg for marker in _EXCLUDED_MARKERS):
        return False
    return any(marker in msg for marker in _MEDIA_ERROR_MARKERS)


def _split_copy(text: str) -> tuple[str, str]:
    """First sentence -> title, next ~240 chars -> body (matches the
    republish-media script convention)."""
    text = " ".join((text or "").split())
    for sep in (". ", "! ", "? "):
        if sep in text:
            title, rest = text.split(sep, 1)
            return title.strip()[:110], rest.strip()[:240]
    return text[:110], ""


async def _healable_posts(db) -> tuple[list[Post], list[Post]]:
    """Return (healable, exhausted) posts for media-failed targets."""
    rows = (
        (
            await db.execute(
                select(PostTarget.post_id)
                .where(PostTarget.status.in_(["skipped", "failed"]))
                .where(PostTarget.error_message.is_not(None))
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return [], []
    posts = (
        (
            await db.execute(
                select(Post)
                .where(Post.id.in_(set(rows)))
                .where(Post.status.in_([PostStatus.FAILED, PostStatus.SUPPRESSED]))
            )
        )
        .scalars()
        .all()
    )
    healable: list[Post] = []
    exhausted: list[Post] = []
    for post in posts:
        targets = (
            await db.execute(select(PostTarget).where(PostTarget.post_id == post.id))
        ).scalars().all()
        if not any(_is_media_failure(t.error_message) for t in targets):
            continue
        if any(t.status == "published" for t in targets):
            continue
        attempts = int((post.meta_data or {}).get("media_heal_attempts", 0) or 0)
        if attempts >= _MAX_HEAL_ATTEMPTS:
            exhausted.append(post)
        else:
            healable.append(post)
    return healable[:_MAX_POSTS_PER_RUN], exhausted


async def _heal() -> tuple[list[tuple[str, list[str]]], list[str]]:
    """Regenerate branded media + attach for each healable post.

    Returns (publish_jobs, exhausted_post_ids) — publish_jobs are dispatched
    by the sync task body via ``publish_post_now`` after commit.
    """
    publish_jobs: list[tuple[str, list[str]]] = []
    exhausted_ids: list[str] = []
    async with task_session() as db:
        healable, exhausted = await _healable_posts(db)
        for post in exhausted:
            meta = dict(post.meta_data or {})
            if not meta.get("media_heal_alerted"):
                meta["media_heal_alerted"] = True
                post.meta_data = meta
                exhausted_ids.append(str(post.id))
        # Durable alert flags before the heal loop — a later per-post
        # rollback must not resurrect the Slack alert.
        await db.commit()
        healed = 0
        for post in healable:
            try:
                title, body = _split_copy(post.content_text or "")
                if not title:
                    continue
                attempts_done = int((post.meta_data or {}).get("media_heal_attempts", 0) or 0)
                bg_img = None
                if attempts_done + 1 >= _MAX_HEAL_ATTEMPTS:
                    # Final attempt: plain brand slides already failed QA —
                    # generate an on-topic FLUX background (CF-first,
                    # ComfyUI fallback, garbled-text pixel QA) blended under
                    # the brand canvas so the image depicts the copy.
                    image_prompt = f"{title}, professional business photography concept"
                    try:
                        bg_img = await _cf_generate_background(image_prompt, CF_TXT2IMG_FREE[0])
                        if bg_img is not None:
                            bg_img = await _qa_generated_background(
                                bg_img, image_prompt, CF_TXT2IMG_FREE[0], "media-heal"
                            )
                    except Exception as exc:
                        logger.info("media-heal bg generation skipped for %s: %s", post.id, exc)
                        bg_img = None
                img = compose_branded_slide(
                    bg_img,
                    index=1,
                    total=1,
                    slide_type="content",
                    title=title,
                    body=body,
                    motif="cloud",
                )
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                asset = await persist_generated_image(
                    db,
                    team_id=post.team_id,
                    user_id=post.user_id,
                    image_bytes=buf.getvalue(),
                    prompt=f"media-heal: {title}",
                    source="media-heal",
                    extension=".png",
                    max_edge=0,
                )
                meta = dict(post.meta_data or {})
                meta["media_heal_attempts"] = int(meta.get("media_heal_attempts", 0) or 0) + 1
                meta["media_source"] = "branded-slide-heal"
                meta["media_healed_at"] = datetime.now(UTC).isoformat()
                post.meta_data = meta
                post.media_ids = [asset.id]
                targets = (
                    await db.execute(select(PostTarget).where(PostTarget.post_id == post.id))
                ).scalars().all()
                account_ids = [str(t.social_account_id) for t in targets]
                pid_str = str(post.id)
                # Per-post commit: only dispatch publish jobs for work that
                # actually landed — a flush/commit failure rolls back this
                # post alone instead of poisoning the whole sweep.
                await db.commit()
                healed += 1
                if account_ids:
                    publish_jobs.append((pid_str, account_ids))
            except Exception as exc:
                logger.warning("media-heal failed for post %s: %s", post.id, exc)
                try:
                    await db.rollback()
                except Exception:
                    pass
    logger.info("media-heal: healed=%d exhausted=%d", healed, len(exhausted_ids))
    return publish_jobs, exhausted_ids


@shared_task
def run_media_heal_sweep() -> dict:
    """Every-6h sweep: brand-fix media-failed posts and requeue them."""
    try:
        publish_jobs, exhausted_ids = run_async(_heal())
    except Exception as exc:
        logger.warning("media-heal sweep failed: %s", exc)
        return {"error": str(exc)}

    queued = []
    for post_id, account_ids in publish_jobs:
        try:
            result = publish_post_now(post_id, account_ids)
            if result.get("success"):
                queued.append(post_id)
        except Exception as exc:
            logger.warning("media-heal publish dispatch failed for %s: %s", post_id, exc)

    if exhausted_ids:
        try:
            run_async(
                post_alert_to_slack(
                    ":warning: media-heal: posts still failing media QA after "
                    f"{_MAX_HEAL_ATTEMPTS} branded-media attempts — needs a human "
                    f"copy/media decision: {', '.join(exhausted_ids)}"
                )
            )
        except Exception as exc:
            logger.warning("media-heal alert failed: %s", exc)

    # media_assets rows feed the D1 mirror used by edge reads.
    if publish_jobs or exhausted_ids:
        try:
            from app.services.db_sync import sync_after_worker_task

            run_async(sync_after_worker_task(["posts", "media_assets", "post_targets", "publish_queue"]))
        except Exception as exc:
            logger.warning("media-heal d1 sync failed: %s", exc)

    return {"healed": len(publish_jobs), "queued": queued, "exhausted": exhausted_ids}
