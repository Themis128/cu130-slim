"""Ledger hygiene — compact old rows, keep the learning signal.

Rows >30d: null out post_text + comment_draft (the bulky fields) but
keep platform/author/action/score/status — outcomes stay queryable for
the reciprocity loop and comment-style fine-tuning.
"""

import logging

from celery import shared_task
from sqlalchemy import update

logger = logging.getLogger(__name__)


@shared_task(name="app.worker.tasks.engagement_archive.compact_ledger", bind=True, max_retries=0)
def compact_ledger(self, days: int = 30):
    from app.worker._async import run_async

    return run_async(_run(days))


async def _run(days: int):
    import datetime as dt

    from app.db.session import async_session_maker
    from app.models.engagement import EngagementAction, EngagementCandidate

    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
    async with async_session_maker() as db:
        a = await db.execute(update(EngagementAction).where(EngagementAction.created_at < cutoff).values(comment_draft=None))
        c = await db.execute(update(EngagementCandidate).where(EngagementCandidate.ingested_at < cutoff).values(post_text=""))
        await db.commit()
    logger.info("[ledger] compacted: %d actions, %d candidates", a.rowcount, c.rowcount)
    return {"actions_pruned": a.rowcount, "candidates_pruned": c.rowcount}
