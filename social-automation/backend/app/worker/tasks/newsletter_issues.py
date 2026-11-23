"""Celery tasks — LinkedIn newsletter issue dispatch + live verification.

``dispatch_newsletter_issues`` (beat every 5 min): claims due ``queued``
rows and publishes them through the sidecar→bridge fallback. Serialized by
a Redis mutex so a long publish can't overlap the next sweep, and by
``FOR UPDATE SKIP LOCKED`` so concurrent workers can't claim the same row.

``verify_newsletter_issues`` (beat every 15 min): re-loads published
``article_url`` pages in the sidecar browser and stamps ``verified_at``
when the issue title is found — the platform-side replacement for the
shell ``newsletter_verify_published.sh``.
"""

import logging

from celery import shared_task

from app.worker._async import run_async, task_session

celery_app = __import__("app.worker.celery_app", fromlist=["celery_app"]).celery_app
celery_app.set_default()
celery_app.set_current()

logger = logging.getLogger(__name__)


async def _dispatch_one(issue) -> dict:
    """Publish one claimed issue and record the outcome in a fresh session."""
    from app.services import newsletter_issue_queue as q
    from app.services import newsletter_publisher

    try:
        async with task_session() as db:
            result, via, _warnings = await newsletter_publisher.publish_stored_issue(issue, db)
    except Exception as exc:  # noqa: BLE001 — any publish failure → retry/failed
        async with task_session() as db:
            updated = await q.record_publish_failure(db, issue.id, f"{type(exc).__name__}: {exc}")
        return {
            "id": str(issue.id),
            "title": issue.title,
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "status": updated.status.value if updated else "missing",
        }
    async with task_session() as db:
        updated = await q.record_publish_result(db, issue.id, result, via)
    return {
        "id": str(issue.id),
        "title": issue.title,
        "ok": True,
        "via": via,
        "published": bool((result or {}).get("published")),
        "scheduled": bool((result or {}).get("scheduled")),
        "article_url": (result or {}).get("article_url"),
        "status": updated.status.value if updated else "missing",
    }


async def _dispatch() -> dict:
    from app.services import newsletter_issue_queue as q

    token = await q.acquire_dispatch_lock()
    if token is None:
        logger.info("newsletter dispatch skipped — lock held by another run")
        return {"skipped": "lock held", "claimed": 0, "results": []}
    try:
        async with task_session() as db:
            claimed = await q.claim_due(db, limit=1)
        # Publish outside the DB session — a sidecar publish takes minutes.
        results = [await _dispatch_one(issue) for issue in claimed]
        return {"claimed": len(claimed), "results": results}
    finally:
        await q.release_dispatch_lock(token)


@shared_task
def dispatch_newsletter_issues() -> dict:
    """Claim and publish due newsletter issues (one per sweep, sequential)."""
    try:
        return run_async(_dispatch())
    except Exception as exc:  # noqa: BLE001
        logger.warning("newsletter issue dispatch failed: %s", exc)
        return {"error": str(exc)}


async def _verify_one(issue) -> dict:
    from app.services import newsletter_issue_queue as q

    async with task_session() as db:
        fresh = await q.get_issue(db, issue.id)
        if fresh is None:
            return {"id": str(issue.id), "error": "missing"}
        result = await q.verify_issue_article(db, fresh)
    return {"id": str(issue.id), "title": issue.title, **result}


async def _verify() -> dict:
    from app.services import newsletter_issue_queue as q

    token = await q.acquire_dispatch_lock()
    if token is None:
        logger.info("newsletter verify skipped — dispatch/verify lock held")
        return {"skipped": "lock held", "checked": 0, "results": []}
    try:
        async with task_session() as db:
            issues = await q.list_issues_needing_verify(db, limit=2)
        # Verify outside long transactions; each check is a browser navigate.
        results = []
        for issue in issues:
            results.append(await _verify_one(issue))
        return {"checked": len(results), "results": results}
    finally:
        await q.release_dispatch_lock(token)


@shared_task
def verify_newsletter_issues() -> dict:
    """Verify published article_urls live through the sidecar browser."""
    try:
        return run_async(_verify())
    except Exception as exc:  # noqa: BLE001
        logger.warning("newsletter issue verify failed: %s", exc)
        return {"error": str(exc)}
