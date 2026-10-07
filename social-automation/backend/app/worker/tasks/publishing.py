import asyncio
import dataclasses
import hashlib
import logging
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
from celery import shared_task
from sqlalchemy import and_, delete, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.security import decrypt_token
from app.models.content import MediaAsset, Post, PostStatus, PostTarget
from app.models.queue import PublishQueue, QueueStatus
from app.models.social_account import SocialAccount
from app.services.db_sync import sync_after_worker_task
from app.services.duplicate_detector import is_duplicate
from app.services.instagram_api import InstagramAPIClient
from app.services.publishing import (
    PublishResult,
    _instagram_find_live,
    _resolve_ig_user_token,
    publish_to_platform,
)
from app.services.slack_notifications import (
    post_alert_to_slack,
    post_publishing_to_slack,
    publish_failure_buttons,
)
from app.services.spellcheck import auto_correct
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

_DEFER_STATE_KEY = "publish_defer"
_SWEEP_STATE_KEY = "publish_defer_sweep"


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _set_platform_specific(post: Post, ps: dict) -> None:
    post.platform_specific = ps
    try:
        flag_modified(post, "platform_specific")
    except Exception:  # noqa: BLE001 — plain objects in unit tests
        pass


def _apply_capacity_deferral(
    *,
    item: PublishQueue,
    target: PostTarget | None,
    post: Post,
    account_id: object,
    retry_after: datetime,
    error: str | None,
    now: datetime,
    max_hours: float,
    max_count: int,
) -> str | None:
    """Defer a capacity-blocked target (X credits/429, fallback cap/gap/breaker).

    Keeps the target ``pending`` and re-schedules the queue row for
    ``retry_after`` (clamped to [now+1min, deadline]) without burning an
    attempt. Bounded by ``max_hours`` since the queue row was created and
    ``max_count`` deferrals of that row — past either bound returns the
    give-up error (the caller then fails the target permanently + alerts).
    Other targets of the post have their own queue rows and are unaffected.
    """
    created = _aware(getattr(item, "created_at", None)) or now
    deadline = created + timedelta(hours=max_hours)
    ps = dict(post.platform_specific or {})
    states = dict(ps.get(_DEFER_STATE_KEY) or {})
    key = str(account_id)
    state = dict(states.get(key) or {})
    if state.get("queue_id") != str(item.id):
        state = {"queue_id": str(item.id), "count": 0, "first_at": now.isoformat()}
    count = int(state.get("count") or 0)
    detail = (error or "capacity unavailable").strip()
    if now >= deadline or count >= max_count:
        return (
            f"X capacity deferral limit reached — gave up after {count} deferral(s) over "
            f"{(now - created).total_seconds() / 3600:.1f}h (max {max_hours:g}h / {max_count}). "
            f"Last error: {detail}"
        )[:1000]
    when = max(_aware(retry_after) or now, now + timedelta(minutes=1))
    when = min(when, deadline)
    state.update(
        count=count + 1,
        last_at=now.isoformat(),
        next_at=when.isoformat(),
        last_error=detail[:300],
    )
    states[key] = state
    ps[_DEFER_STATE_KEY] = states
    _set_platform_specific(post, ps)

    item.status = QueueStatus.PENDING
    item.scheduled_at = when
    item.locked_at = None
    item.locked_by = None
    if target is not None:
        target.status = "pending"
        target.error_message = (
            f"Deferred #{count + 1} until {when:%Y-%m-%d %H:%M} UTC: {detail}"
        )[:1000]
    return None


def _clear_deferral_state(post: Post, account_id: object) -> None:
    ps = post.platform_specific or {}
    states = ps.get(_DEFER_STATE_KEY) or {}
    if str(account_id) in states:
        ps = dict(ps)
        states = dict(states)
        states.pop(str(account_id), None)
        if states:
            ps[_DEFER_STATE_KEY] = states
        else:
            ps.pop(_DEFER_STATE_KEY, None)
        _set_platform_specific(post, ps)


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
    targets_summary: str | None = None,
) -> None:
    """Best-effort Slack alert for final publish failures (never raises)."""
    post_id = str(getattr(post, "id", "") or "unknown")
    platform = getattr(account, "platform", None) or "unknown"
    queue_id = str(getattr(queue_item, "id", "") or "unknown")
    reason_clean = (reason or "").replace("\n", " ").strip()
    preview = _content_preview(post) if post else ""
    lines = [
        "*We couldn’t publish a post*",
    ]
    if preview:
        lines.append(f"> {preview}")
    lines.append(
        "• What to do next: open SocialAuto → Posts → Failed, then retry (or reconnect the account if needed)."
    )
    lines.append(f"• What happened: {reason_clean[:300] or 'Unknown error'}")
    if targets_summary:
        lines.append(f"• Other platforms: {targets_summary}")
    lines += [
        "",
        "*Details*",
        f"• post: `{post_id}`",
        f"• platform: `{platform}`",
        f"• queue item: `{queue_id}`",
        "_Cloudless · Clear skies. Zero friction._",
    ]
    text = "\n".join(lines)[:2000]
    blocks: list[dict] = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
    blocks += publish_failure_buttons(queue_id)
    await post_alert_to_slack(text, blocks=blocks)


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


def _content_preview(post: Post, limit: int = 140) -> str:
    """Short excerpt of the post copy for Slack context."""
    text = " ".join((getattr(post, "content_text", "") or "").split())
    # Slack mrkdwn entity-escape: post copy containing `<!channel>`,
    # `<@U…>`, or link syntax must not ping users or reformat as links.
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return text[: limit - 1] + "…" if len(text) > limit else text


async def _post_publish_summary_to_slack(
    post: Post, results: list[tuple[str, str | None]]
) -> None:
    """One aggregated Slack message per post — platforms + links on one line
    each — instead of a fan-out of one message per platform target."""
    try:
        lines = ["*Published*"]
        preview = _content_preview(post)
        if preview:
            lines.append(f"> {preview}")
        for platform, url in results:
            link = url or "n/a"
            lines.append(f"• *{platform}*: {link}")
        lines += [
            "",
            "*Details*",
            f"• post: `{post.id}`",
            "_Cloudless · Clear skies. Zero friction._",
        ]
        await post_publishing_to_slack("\n".join(lines)[:2000])
    except Exception:
        logger.debug("Slack publish-summary notification failed (non-fatal)", exc_info=True)


async def _maybe_send_publish_summary(db: AsyncSession, post: Post) -> None:
    """Emit the aggregated '#socialauto-publishing' message once per post.

    Runs after each successful publish, but only fires once the post has no
    queue rows left in flight — so a post whose targets landed in different
    batches (or on different workers) still gets ONE summary covering every
    published platform. Claimed atomically via a conditional UPDATE on
    posts.platform_specific, keyed on the set of published targets: a later
    publish to a *new* account produces a new key and notifies again.
    """
    try:
        active = await db.scalar(
            select(func.count())
            .select_from(PublishQueue)
            .where(
                PublishQueue.post_id == post.id,
                PublishQueue.status.in_([QueueStatus.PENDING, QueueStatus.PROCESSING]),
                # A capacity-deferred target (scheduled in the future) must
                # not hold back the summary of the platforms that did
                # publish; when it lands later it produces a new key.
                PublishQueue.scheduled_at <= datetime.now(UTC),
            )
        )
        if active:
            return
        rows = (
            await db.execute(
                select(PostTarget.platform_url, SocialAccount.id, SocialAccount.platform)
                .join(
                    SocialAccount,
                    PostTarget.social_account_id == SocialAccount.id,
                )
                .where(PostTarget.post_id == post.id, PostTarget.status == "published")
            )
        ).all()
        if not rows:
            return
        claim = hashlib.sha1(
            ",".join(sorted(str(r.id) for r in rows)).encode()
        ).hexdigest()[:16]
        claimed = (
            await db.execute(
                text(
                    "UPDATE posts SET platform_specific ="
                    " COALESCE(platform_specific, '{}'::jsonb) ||"
                    " jsonb_build_object('publish_summary_claim', :claim)"
                    " WHERE id = :pid"
                    " AND platform_specific->>'publish_summary_claim'"
                    " IS DISTINCT FROM :claim"
                ),
                {"claim": claim, "pid": post.id},
            )
        ).rowcount
        await db.commit()
        if not claimed:
            return  # another worker already sent this exact summary
        await _post_publish_summary_to_slack(
            post, [(r.platform, r.platform_url) for r in rows]
        )
    except Exception:
        logger.warning(
            "publish summary skipped for post %s", post.id, exc_info=True
        )


async def _target_status_line(post: Post, db: AsyncSession) -> str | None:
    """Summarise the post's other targets for failure alerts — shows a partial
    success ('published on linkedin, threads') instead of looking fully dead."""
    try:
        result = await db.execute(
            select(PostTarget).where(PostTarget.post_id == post.id)
        )
        targets = result.scalars().all()
    except Exception:
        return None
    buckets: dict[str, list[str]] = {}
    acct_result = await db.execute(
        select(SocialAccount).where(
            SocialAccount.id.in_([t.social_account_id for t in targets])
        )
    )
    accts = {a.id: a.platform for a in acct_result.scalars().all()}
    for t in targets:
        buckets.setdefault(t.status, []).append(accts.get(t.social_account_id, "?"))
    parts = []
    for status in ("published", "failed", "skipped", "pending"):
        names = buckets.get(status)
        if names:
            parts.append(f"{status}: {', '.join(sorted(set(names)))}")
    return " · ".join(parts) if parts else None

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
        # Atomic claim via FOR UPDATE SKIP LOCKED: concurrent queue processors
        # each receive a disjoint set of rows — a second worker skips rows the
        # first already locked, eliminating the duplicate-publish race where two
        # processors SELECT the same PENDING rows before either commits.
        # PROCESSING rows with a stale lock (>15 min) are reclaimed to recover
        # from worker crashes mid-publish.
        now = datetime.now(UTC)
        stale_cutoff = now - timedelta(minutes=15)
        result = await db.execute(
            select(PublishQueue)
            .where(
                PublishQueue.scheduled_at <= now,
                or_(
                    PublishQueue.status == QueueStatus.PENDING,
                    and_(
                        PublishQueue.status == QueueStatus.PROCESSING,
                        PublishQueue.locked_at < stale_cutoff,
                    ),
                ),
            )
            .order_by(PublishQueue.priority.desc(), PublishQueue.scheduled_at.asc())
            .limit(50)
            .with_for_update(skip_locked=True)
        )
        items = result.scalars().all()

        # Collapse duplicate active rows for the same (post, account) claimed in
        # this batch — process the first, complete the rest. The partial unique
        # index ux_publish_queue_active_target prevents new dupes; this guard
        # also covers rows inserted before the index existed.
        seen_pairs: set[tuple] = set()
        dupes: list[PublishQueue] = []
        kept: list[PublishQueue] = []
        for item in items:
            pair = (item.post_id, item.social_account_id)
            if pair in seen_pairs:
                dupes.append(item)
            else:
                seen_pairs.add(pair)
                kept.append(item)
        for dup in dupes:
            dup.status = QueueStatus.COMPLETED
        if dupes:
            logger.warning(
                "[publishing] collapsing %d duplicate queue row(s) this batch",
                len(dupes),
            )
        items = kept

        worker_id = os.environ.get("HOSTNAME", "celery-worker")
        for item in items:
            item.status = QueueStatus.PROCESSING
            item.locked_at = now
            item.locked_by = worker_id
        await db.commit()  # commit once: releases row locks; status keeps them claimed

        for item in items:

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

                target_result = await db.execute(
                    select(PostTarget).where(
                        PostTarget.post_id == post.id,
                        PostTarget.social_account_id == account.id,
                    )
                )
                target = target_result.scalar_one_or_none()

                # Idempotency guard: this target already published (e.g. a
                # stale-lock reclaim or a retry after a lost commit). Never
                # republish — a second external post is worse than a skipped
                # queue row.
                if target and target.status == "published":
                    item.status = QueueStatus.COMPLETED
                    await db.flush()
                    await _rollup_post_status(post, db)
                    await db.commit()
                    continue

                # Duplicate guard: two distinct Post rows with identical copy
                # can land in the same slot (e.g. a re-generated draft). Don't
                # re-post the same content to the same account inside 24h.
                # Recurring posts are exempt — republishing is their point.
                if not post.is_recurring:
                    recent_texts = (
                        await db.execute(
                            select(Post.content_text)
                            .join(PostTarget, PostTarget.post_id == Post.id)
                            .where(
                                PostTarget.social_account_id == account.id,
                                PostTarget.status == "published",
                                PostTarget.published_at >= now - timedelta(hours=24),
                                Post.id != post.id,
                            )
                        )
                    ).scalars().all()
                    if any(
                        is_duplicate(post.content_text or "", t or "", threshold=0.9)
                        for t in recent_texts
                    ):
                        item.status = QueueStatus.COMPLETED
                        if target:
                            target.status = "skipped"
                            target.error_message = (
                                "Skipped: identical content already published to "
                                "this account within the last 24h"
                            )
                        await db.flush()
                        await _rollup_post_status(post, db)
                        await db.commit()
                        continue

                if post.content_text:
                    post.content_text = await auto_correct(post.content_text)

                pub = await publish_to_platform(account, post, db)

                deferred = False
                if not pub.success and getattr(pub, "retry_after", None) is not None:
                    # Soft deferral (X API credits/429, x_web daily cap / min
                    # gap / breaker): re-queue for when capacity returns
                    # without burning an attempt — bounded by age/count.
                    cfg = get_settings()
                    gave_up = _apply_capacity_deferral(
                        item=item,
                        target=target,
                        post=post,
                        account_id=account.id,
                        retry_after=pub.retry_after,
                        error=pub.error,
                        now=datetime.now(UTC),
                        max_hours=float(getattr(cfg, "PUBLISH_DEFER_MAX_HOURS", 72.0)),
                        max_count=int(getattr(cfg, "PUBLISH_DEFER_MAX_COUNT", 500)),
                    )
                    if gave_up is None:
                        deferred = True
                        logger.info(
                            "[publishing] deferred %s target of post %s until %s: %s",
                            account.platform, post.id, item.scheduled_at, (pub.error or "")[:200],
                        )
                    else:
                        pub = dataclasses.replace(
                            pub, error=gave_up, permanent=True, retry_after=None, skipped=False,
                        ) if isinstance(pub, PublishResult) else PublishResult(
                            success=False, error=gave_up, permanent=True,
                        )

                if deferred:
                    pass
                elif pub.success:
                    _clear_deferral_state(post, account.id)
                    item.status = QueueStatus.COMPLETED
                    if target:
                        target.status = "published"
                        target.platform_post_id = pub.platform_post_id
                        target.platform_url = pub.platform_url
                        target.published_at = datetime.now(UTC)
                    if getattr(pub, "platform_meta", None):
                        ps = dict(post.platform_specific or {})
                        for key, value in (pub.platform_meta or {}).items():
                            if isinstance(value, dict):
                                ps[key] = {**(ps.get(key) or {}), **value}
                            else:
                                ps[key] = value
                        post.platform_specific = ps
                        flag_modified(post, "platform_specific")
                    # Webhook + author email stay per-target (downstream
                    # systems correlate per platform). The aggregated Slack
                    # summary fires after commit via _maybe_send_publish_summary.
                    await _notify_publish_success(post, account, pub.platform_url)
                elif getattr(pub, "skipped", False):
                    # Soft-skip: do not retry, do not fail the whole post.
                    item.status = QueueStatus.COMPLETED
                    if target:
                        target.status = "skipped"
                        target.error_message = (
                            pub.error or "skipped by platform pre-flight (no detail recorded)"
                        )
                else:
                    if getattr(pub, "permanent", False):
                        # Deterministic failure (invalid media, tripped
                        # safety breaker): retrying cannot help.
                        item.attempts = max(item.attempts, item.max_attempts - 1)
                    item.attempts += 1
                    if item.attempts >= item.max_attempts:
                        item.status = QueueStatus.FAILED
                        prev_error = (target.error_message or "").strip() if target else ""
                        if target:
                            target.status = "failed"
                            target.error_message = pub.error
                            if pub.platform_post_id:
                                target.platform_post_id = pub.platform_post_id
                        if getattr(pub, "platform_meta", None):
                            ps = dict(post.platform_specific or {})
                            for key, value in (pub.platform_meta or {}).items():
                                if isinstance(value, dict):
                                    ps[key] = {**(ps.get(key) or {}), **value}
                                else:
                                    ps[key] = value
                            post.platform_specific = ps
                            flag_modified(post, "platform_specific")
                        # Dedup: don't re-alert when a manual retry fails with
                        # the identical error (same root cause, same fix needed).
                        new_error = (pub.error or "unknown publish error").strip()
                        if new_error != prev_error:
                            await _notify_publish_failure(
                                post=post,
                                account=account,
                                queue_item=item,
                                reason=pub.error or "unknown publish error",
                                targets_summary=await _target_status_line(post, db),
                            )
                        if getattr(pub, "ambiguous", False) and account.platform == "instagram":
                            # The post may be live despite the error — feed
                            # indexing can lag minutes beyond the in-path
                            # checks. Re-verify a few times before settling.
                            reconcile_instagram_publish.apply_async(
                                args=[str(post.id), str(account.id)],
                                countdown=300,
                            )
                    else:
                        item.status = QueueStatus.PENDING
                        item.locked_at = None
                        item.locked_by = None

                # Ensure rollup queries see the in-memory changes.
                await db.flush()
                await _rollup_post_status(post, db)
                await db.commit()
                if pub.success:
                    # Aggregated Slack summary — one per post, deferred until
                    # no queue rows remain in flight so split batches/workers
                    # still produce a single all-platform message.
                    await _maybe_send_publish_summary(db, post)

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
                err_prev_error = ""
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
                                err_prev_error = (err_target.error_message or "").strip()
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

                if item.status == QueueStatus.FAILED and (
                    "Unhandled exception while publishing (see worker logs)" != err_prev_error
                ):
                    await _notify_publish_failure(
                        post=err_post,
                        account=err_account,
                        queue_item=item,
                        reason="Unhandled exception while publishing (see worker logs)",
                        targets_summary=(
                            await _target_status_line(err_post, db) if err_post else None
                        ),
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


# Error texts written by the pre-deferral code (soft "skip" / fail) for X
# capacity problems. Only these are re-queued by the safety-net sweep.
_X_CAPACITY_ERROR_MARKERS = (
    "monthly write quota",
    "api credits exhausted",
    "credits depleted",
    "credits-depleted",
    "usagecapexceeded",
    "usage-capped",
    "daily cap reached",
    "circuit breaker open",
    "minimum gap between posts",
    "x write capacity unavailable",
    "x video posts need the x web fallback",
)
# Never re-queue these even if a capacity marker also appears.
_X_SWEEP_EXCLUDE_MARKERS = (
    "deferral limit reached",
    "circuit breaker tripped",
    "duplicate",
    "identical content",
    "media invalid",
    "media incomplete",
    "media upload",
    "logged in as @",
    "cookies belong to",
    "no text content",
    "may or may not be live",
)


def _is_x_capacity_error(text: str | None) -> bool:
    low = (text or "").lower()
    if not low or any(m in low for m in _X_SWEEP_EXCLUDE_MARKERS):
        return False
    return any(m in low for m in _X_CAPACITY_ERROR_MARKERS)


async def _requeue_capacity_stuck_x_async(hours: float = 72.0, limit: int = 20) -> dict:
    """Safety net: re-queue X targets stuck by an old capacity skip/fail.

    Conservative by design — a target is re-queued at most once (marker in
    posts.platform_specific), and only when: account is X, target is
    failed/skipped with a capacity-type error, no tweet id was recorded, the
    post is still PUBLISHED/FAILED (not archived/draft), its last queue row
    for that account is younger than ``hours``, no queue row is in flight,
    and every attached media asset still exists. The normal publish path
    re-validates media/identity/duplicates before anything is posted.
    """
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=hours)
    requeued: list[str] = []
    async with _worker_db() as db:
        rows = (
            await db.execute(
                select(PostTarget, Post, SocialAccount)
                .join(Post, Post.id == PostTarget.post_id)
                .join(SocialAccount, SocialAccount.id == PostTarget.social_account_id)
                .where(
                    SocialAccount.platform == "twitter",
                    PostTarget.status.in_(["failed", "skipped"]),
                    PostTarget.platform_post_id.is_(None),
                    Post.status.in_([PostStatus.PUBLISHED, PostStatus.FAILED]),
                    Post.updated_at >= cutoff,
                )
                .limit(200)
            )
        ).all()
        for target, post, account in rows:
            if len(requeued) >= limit:
                break
            if not _is_x_capacity_error(target.error_message):
                continue
            key = str(account.id)
            ps = dict(post.platform_specific or {})
            swept = dict(ps.get(_SWEEP_STATE_KEY) or {})
            if key in swept:
                continue
            pair = (
                PublishQueue.post_id == post.id,
                PublishQueue.social_account_id == account.id,
            )
            active = await db.scalar(
                select(func.count()).select_from(PublishQueue).where(
                    *pair,
                    PublishQueue.status.in_([QueueStatus.PENDING, QueueStatus.PROCESSING]),
                )
            )
            if active:
                continue
            last_row = await db.scalar(
                select(func.max(PublishQueue.created_at)).where(*pair)
            )
            last_at = _aware(last_row)
            if last_at is None or last_at < cutoff:
                continue
            media_ids = list(dict.fromkeys(post.media_ids or []))
            if media_ids:
                found = await db.scalar(
                    select(func.count()).select_from(MediaAsset).where(MediaAsset.id.in_(media_ids))
                )
                if int(found or 0) != len(media_ids):
                    continue
            swept[key] = {"at": now.isoformat(), "error": (target.error_message or "")[:300]}
            ps[_SWEEP_STATE_KEY] = swept
            _set_platform_specific(post, ps)
            target.status = "pending"
            target.error_message = (
                f"Re-queued by X capacity sweep (was: {target.error_message or ''})"
            )[:1000]
            db.add(
                PublishQueue(
                    post_id=post.id,
                    social_account_id=account.id,
                    scheduled_at=now,
                    priority=3,
                    status=QueueStatus.PENDING,
                )
            )
            await db.flush()
            await _rollup_post_status(post, db)
            await db.commit()
            requeued.append(str(post.id))
            logger.warning("[publishing] X capacity sweep re-queued post %s account %s", post.id, account.id)
    return {"requeued": requeued}


async def _cleanup_publish_queue_async(days: int = 3) -> dict:
    async with _worker_db() as db:
        cutoff = datetime.now(UTC) - timedelta(days=days)
        result = await db.execute(
            delete(PublishQueue).where(
                PublishQueue.status.in_([QueueStatus.FAILED, QueueStatus.CANCELLED]),
                PublishQueue.created_at < cutoff,
            )
        )
        await db.commit()
        deleted = result.rowcount or 0
        logger.info("Publish queue cleanup: deleted %s terminal rows older than %sd", deleted, days)
        return {"deleted": deleted, "older_than_days": days}


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


@shared_task
def requeue_capacity_stuck_x_targets() -> dict:
    if not getattr(get_settings(), "X_CAPACITY_SWEEP_ENABLED", True):
        return {"requeued": [], "disabled": True}
    hours = float(getattr(get_settings(), "PUBLISH_DEFER_MAX_HOURS", 72.0))
    result = asyncio.run(_requeue_capacity_stuck_x_async(hours=hours))
    if result["requeued"]:
        asyncio.run(sync_after_worker_task(["posts", "post_targets", "publish_queue"]))
    return result


@shared_task
def cleanup_publish_queue(days: int = 3) -> dict:
    result = asyncio.run(_cleanup_publish_queue_async(days))
    if result["deleted"]:
        asyncio.run(sync_after_worker_task(["publish_queue"]))
    return result


@shared_task(bind=True, max_retries=3)
def reconcile_instagram_publish(self, post_id: str, social_account_id: str) -> dict:
    """Re-check the IG feed ~5 min after a publish-boundary failure.

    Covers the tail of the 2207051 false-negative: the post can be live
    server-side while the /media list takes minutes to index it — too long
    for the in-path settle checks. Retries at ~5/10/15/20 min; if a matching
    recent caption is found the target (and post) are reconciled to
    published with the real media id instead of staying failed.
    """
    outcome = asyncio.run(_reconcile_instagram_publish_async(post_id, social_account_id))
    if outcome.get("recovered"):
        asyncio.run(sync_after_worker_task(["posts", "post_targets", "publish_queue"]))
        return outcome
    if outcome.get("retry"):
        raise self.retry(countdown=300)
    return outcome


async def _reconcile_instagram_publish_async(post_id: str, social_account_id: str) -> dict:
    async with _worker_db() as db:
        post = (
            await db.execute(select(Post).where(Post.id == post_id))
        ).scalar_one_or_none()
        account = (
            await db.execute(select(SocialAccount).where(SocialAccount.id == social_account_id))
        ).scalar_one_or_none()
        if not post or not account or account.platform != "instagram":
            return {"recovered": False, "reason": "post or instagram account not found"}
        target = (
            await db.execute(
                select(PostTarget).where(
                    PostTarget.post_id == post.id,
                    PostTarget.social_account_id == account.id,
                )
            )
        ).scalar_one_or_none()
        if not target or target.status == "published":
            return {"recovered": False, "reason": "nothing to reconcile"}

        meta = account.meta_data or {}
        try:
            token = await _resolve_ig_user_token(
                decrypt_token(account.access_token_enc), account, db
            )
            client = InstagramAPIClient(
                access_token=token,
                ig_user_id=account.account_id,
                use_business_login_api=(meta.get("login_type") == "business_login"),
            )
            live = await _instagram_find_live(
                client, post.content_text or "", within_minutes=60
            )
        except Exception as exc:
            logger.warning("Instagram reconcile probe failed for post %s: %s", post_id, exc)
            live = None
        if not live:
            # Still not on the feed — could be a real failure or very slow
            # indexing; let the task retry a few times before giving up.
            return {"recovered": False, "retry": True}

        target.status = "published"
        target.platform_post_id = str(live.get("id") or "")
        target.platform_url = live.get("permalink")
        target.published_at = datetime.now(UTC)
        target.error_message = None
        # A queue row left in terminal FAILED would keep the post flagged —
        # the publish actually landed, so close it out too.
        q = (
            await db.execute(
                select(PublishQueue).where(
                    PublishQueue.post_id == post.id,
                    PublishQueue.social_account_id == account.id,
                    PublishQueue.status == QueueStatus.FAILED,
                )
            )
        ).scalars().first()
        if q:
            q.status = QueueStatus.COMPLETED
        await _rollup_post_status(post, db)
        await db.commit()
        logger.info(
            "Instagram reconcile recovered post %s → media %s",
            post_id, live.get("id"),
        )
        return {"recovered": True, "media_id": live.get("id")}
