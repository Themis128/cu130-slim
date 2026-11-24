"""Newsletter issue queue — enqueue, claim, record, verify.

DB state machine for ``newsletter_issues`` consumed by the
``newsletter-issue-dispatch`` / ``newsletter-issue-verify`` Celery beats
and the ``/api/v1/newsletter/issues`` endpoints. Publishing itself lives in
``newsletter_publisher``; this module owns persistence + dedup + the
Redis dispatch mutex that prevents overlapping sidecar publishes.
"""

from __future__ import annotations

import asyncio
import logging
import re
import socket
import time
import unicodedata
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.newsletter_issue import NewsletterIssue, NewsletterIssueStatus

logger = logging.getLogger(__name__)

MAX_PUBLISH_ATTEMPTS = 3
# A row stuck in 'publishing' longer than this is assumed orphaned (worker
# crash / sidecar hang past the celery soft limit) and re-queued.
STALE_CLAIM_AFTER = timedelta(minutes=45)
# Backoff between retries: 30min * attempt number.
RETRY_BACKOFF = timedelta(minutes=30)
# A "circuit open until <ts>" sidecar failure is infra-side, not a content
# failure — the issue waits out the cooldown instead of burning its publish
# attempts. Extra runway on top of MAX_PUBLISH_ATTEMPTS covers a multi-hour
# circuit; beyond this cap the row fails like any other error.
MAX_CIRCUIT_WAIT_ATTEMPTS = MAX_PUBLISH_ATTEMPTS + 5
# Re-dispatch this far past the circuit's `until` instant.
CIRCUIT_WAIT_GRACE = timedelta(minutes=2)
_CIRCUIT_OPEN_RE = re.compile(r"circuit open until (\S+)")
VERIFY_MAX_ATTEMPTS = 8
VERIFY_LOOKBACK = timedelta(days=14)

_DISPATCH_LOCK_KEY = "newsletter:issue-dispatch:lock"
_DISPATCH_LOCK_TTL = 2400  # 40 min — longer than the 15-min sidecar timeout

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify_issue(title: str, issue_number: int | None = None, explicit: str | None = None) -> str:
    """Stable dedup slug. Explicit caller slug wins (validated)."""
    if explicit:
        slug = _SLUG_RE.sub("-", explicit.strip().lower()).strip("-")[:180]
        if slug:
            return slug
    base = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    slug = _SLUG_RE.sub("-", base.lower()).strip("-")[:120] or "issue"
    if issue_number:
        slug = f"{slug}-issue-{issue_number}"
    return slug[:220]


async def enqueue_issue(
    db: AsyncSession,
    *,
    title: str,
    html_body: str,
    slug: str | None = None,
    newsletter_name: str = "The Cloudless Build Log",
    seo_title: str | None = None,
    seo_description: str | None = None,
    post_description: str | None = None,
    issue_number: int | None = None,
    category: str | None = None,
    scheduled_for: datetime | None = None,
    dry_run: bool = False,
    cover_base64: str | None = None,
    render_cover: bool = True,
) -> tuple[NewsletterIssue, bool]:
    """Insert a queued issue. Returns ``(issue, created)`` — idempotent on slug."""
    issue = NewsletterIssue(
        id=uuid.uuid4(),
        slug=slugify_issue(title, issue_number, explicit=slug),
        title=title,
        html_body=html_body,
        newsletter_name=newsletter_name,
        seo_title=seo_title,
        seo_description=seo_description,
        post_description=post_description,
        issue_number=issue_number,
        category=category,
        scheduled_for=scheduled_for or datetime.now(UTC),
        dry_run=dry_run,
        cover_base64=cover_base64,
        render_cover=render_cover,
    )
    db.add(issue)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = (await db.execute(select(NewsletterIssue).where(NewsletterIssue.slug == issue.slug))).scalar_one_or_none()
        if existing is None:
            raise
        return existing, False
    await db.refresh(issue)
    return issue, True


async def get_issue(db: AsyncSession, issue_id: uuid.UUID) -> NewsletterIssue | None:
    return (await db.execute(select(NewsletterIssue).where(NewsletterIssue.id == issue_id))).scalar_one_or_none()


async def list_issues(
    db: AsyncSession,
    *,
    status: str | None = None,
    limit: int = 50,
) -> list[NewsletterIssue]:
    stmt = select(NewsletterIssue).order_by(NewsletterIssue.created_at.desc()).limit(min(limit, 200))
    if status:
        stmt = stmt.where(NewsletterIssue.status == NewsletterIssueStatus(status))
    return list((await db.execute(stmt)).scalars().all())


async def _recover_stale_claims(db: AsyncSession) -> int:
    """Re-queue 'publishing' rows whose claim went stale (crash mid-publish)."""
    cutoff = datetime.now(UTC) - STALE_CLAIM_AFTER
    res = await db.execute(
        update(NewsletterIssue)
        .where(
            NewsletterIssue.status == NewsletterIssueStatus.PUBLISHING,
            NewsletterIssue.claimed_at < cutoff,
        )
        .values(
            status=NewsletterIssueStatus.QUEUED,
            scheduled_for=datetime.now(UTC),
            last_error="stale claim recovered — re-queued",
        )
    )
    await db.commit()
    return res.rowcount or 0


async def claim_due(db: AsyncSession, *, limit: int = 1) -> list[NewsletterIssue]:
    """Claim up to ``limit`` due queued issues for publishing.

    ``FOR UPDATE SKIP LOCKED`` makes concurrent dispatchers safe — each
    row is claimed by exactly one worker. Status flips to PUBLISHING in
    the same transaction so a crash leaves a recoverable stale claim.
    """
    await _recover_stale_claims(db)
    now = datetime.now(UTC)
    stmt = (
        select(NewsletterIssue)
        .where(
            NewsletterIssue.status == NewsletterIssueStatus.QUEUED,
            NewsletterIssue.scheduled_for <= now,
        )
        .order_by(NewsletterIssue.scheduled_for)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    for row in rows:
        row.status = NewsletterIssueStatus.PUBLISHING
        row.claimed_at = now
        row.attempts += 1
    await db.commit()
    return rows


async def record_publish_result(
    db: AsyncSession,
    issue_id: uuid.UUID,
    result: dict,
    via: str,
) -> NewsletterIssue | None:
    issue = await get_issue(db, issue_id)
    if issue is None:
        return None
    merged = dict(issue.result or {})
    merged.update(result or {})
    merged["via"] = via
    issue.result = merged
    issue.article_url = (result or {}).get("article_url") or issue.article_url
    if issue.dry_run or (result or {}).get("dry_run"):
        issue.status = NewsletterIssueStatus.DRYRUN
    elif (result or {}).get("published") or (result or {}).get("scheduled"):
        issue.status = NewsletterIssueStatus.PUBLISHED
        issue.published_at = datetime.now(UTC)
    else:
        issue.status = NewsletterIssueStatus.FAILED
        issue.last_error = "publish returned without published/scheduled flag"
    issue.last_error = (
        ""
        if issue.status
        in (
            NewsletterIssueStatus.PUBLISHED,
            NewsletterIssueStatus.DRYRUN,
        )
        else issue.last_error
    )
    await db.commit()
    return issue


def _circuit_open_until(error: str | None) -> datetime | None:
    """Parse the reopen instant out of a sidecar circuit-open error.

    RATE_LIMITED sidecar failures embed ``LinkedIn circuit open until
    <ISO-8601> (<reason>); skipped <action>``. Retrying before that instant
    only burns an attempt on a fast reject, so the caller schedules just
    past it instead. Returns None when no valid timestamp is embedded.
    """
    m = _CIRCUIT_OPEN_RE.search(error or "")
    if not m:
        return None
    try:
        until = datetime.fromisoformat(m.group(1).rstrip(").,;").replace("Z", "+00:00"))
    except ValueError:
        return None
    if until.tzinfo is None:
        until = until.replace(tzinfo=UTC)
    return max(until, datetime.now(UTC))


async def record_publish_failure(
    db: AsyncSession,
    issue_id: uuid.UUID,
    error: str,
) -> NewsletterIssue | None:
    issue = await get_issue(db, issue_id)
    if issue is None:
        return None
    issue.last_error = (error or "unknown error")[:4000]
    circuit_until = _circuit_open_until(error)
    if circuit_until is not None and issue.attempts < MAX_CIRCUIT_WAIT_ATTEMPTS:
        issue.status = NewsletterIssueStatus.QUEUED
        issue.scheduled_for = circuit_until + CIRCUIT_WAIT_GRACE
    elif issue.attempts >= MAX_PUBLISH_ATTEMPTS:
        issue.status = NewsletterIssueStatus.FAILED
    else:
        issue.status = NewsletterIssueStatus.QUEUED
        issue.scheduled_for = datetime.now(UTC) + RETRY_BACKOFF * issue.attempts
    await db.commit()
    return issue


async def cancel_issue(db: AsyncSession, issue: NewsletterIssue) -> bool:
    if issue.status != NewsletterIssueStatus.QUEUED:
        return False
    issue.status = NewsletterIssueStatus.CANCELLED
    await db.commit()
    return True


async def retry_issue(db: AsyncSession, issue: NewsletterIssue) -> bool:
    if issue.status not in (NewsletterIssueStatus.FAILED, NewsletterIssueStatus.CANCELLED):
        return False
    issue.status = NewsletterIssueStatus.QUEUED
    issue.scheduled_for = datetime.now(UTC)
    issue.last_error = ""
    await db.commit()
    return True


async def mark_due_now(db: AsyncSession, issue: NewsletterIssue) -> None:
    """Pull a queued issue to the front of the next dispatch sweep."""
    if issue.status == NewsletterIssueStatus.QUEUED:
        issue.scheduled_for = datetime.now(UTC)
        await db.commit()


# ── verification ──────────────────────────────────────────────────────


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


async def verify_issue_article(db: AsyncSession, issue: NewsletterIssue) -> dict:
    """Load ``issue.article_url`` in the sidecar browser and check the title.

    Returns ``{"verified": bool, "detail": str}``. On success stamps
    ``verified_at``; failures only bump ``verify_count`` (the sweep stops
    after ``VERIFY_MAX_ATTEMPTS``).
    """
    from app.services.linkedin_sidecar import LinkedInSidecarClient

    if not issue.article_url:
        return {"verified": False, "detail": "no article_url"}
    client = LinkedInSidecarClient()
    try:
        await client.navigate(issue.article_url)
        await asyncio.sleep(4)
        page = await client.page_text()
    except Exception as exc:  # noqa: BLE001 — sidecar down/wedged; retry next sweep
        issue.verify_count += 1
        await db.commit()
        return {"verified": False, "detail": f"sidecar error: {exc}"}

    text = _norm(page.get("text") or "")
    title_norm = _norm(issue.title)
    needle = title_norm[:80] or title_norm
    verified = bool(needle and needle in text)
    detail = "title found on article page" if verified else "title not found on article page"
    issue.verify_count += 1
    if verified:
        issue.verified_at = datetime.now(UTC)
        issue.last_error = ""
    await db.commit()
    return {"verified": verified, "detail": detail, "page_url": page.get("url")}


async def list_issues_needing_verify(db: AsyncSession, *, limit: int = 3) -> list[NewsletterIssue]:
    cutoff = datetime.now(UTC) - VERIFY_LOOKBACK
    stmt = (
        select(NewsletterIssue)
        .where(
            NewsletterIssue.status == NewsletterIssueStatus.PUBLISHED,
            NewsletterIssue.article_url.is_not(None),
            NewsletterIssue.verified_at.is_(None),
            NewsletterIssue.verify_count < VERIFY_MAX_ATTEMPTS,
            NewsletterIssue.published_at >= cutoff,
        )
        .order_by(NewsletterIssue.published_at)
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


# ── dispatch mutex (Redis SET NX EX) ─────────────────────────────────


async def acquire_dispatch_lock() -> str | None:
    """Take the newsletter dispatch mutex. Returns a token, or None if held.

    Best-effort: Redis failure returns a sentinel token so dispatch still
    runs (celery itself needs Redis, so a dead broker means no task anyway).
    """
    token = f"{socket.gethostname()}:{time.time()}"
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(get_settings().REDIS_URL, decode_responses=True)
        try:
            ok = await r.set(_DISPATCH_LOCK_KEY, token, nx=True, ex=_DISPATCH_LOCK_TTL)
        finally:
            await r.aclose()
        return token if ok else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("newsletter dispatch lock unavailable (%s) — proceeding", exc)
        return token


async def release_dispatch_lock(token: str | None) -> None:
    if not token:
        return
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(get_settings().REDIS_URL, decode_responses=True)
        try:
            current = await r.get(_DISPATCH_LOCK_KEY)
            if current == token:
                await r.delete(_DISPATCH_LOCK_KEY)
        finally:
            await r.aclose()
    except Exception as exc:  # noqa: BLE001
        logger.debug("newsletter dispatch lock release failed: %s", exc)
