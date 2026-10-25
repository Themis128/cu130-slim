"""Reciprocity loop — recompute candidate tiers from real outcomes.

Tiers feed score_candidate()'s RYR weighting:
  high   = author is in EspoCRM (lead/contact) OR engaged back >=2x
  medium = is a connection/followed
  low    = cold
Runs daily; reads engagement_actions (outbound) + espocrm lead handles.
"""
import logging

from celery import shared_task
from sqlalchemy import func, select

logger = logging.getLogger(__name__)

@shared_task(name="app.worker.tasks.engagement_tiers.update_connection_tiers",
             bind=True, max_retries=1)
def update_connection_tiers(self):
    from app.worker._async import run_async
    return run_async(_run())


async def _run():
    from app.db.session import async_session_maker
    from app.models.engagement import EngagementAction, EngagementCandidate

    async with async_session_maker() as db:
        # outbound volume per author — authors we've engaged get reciprocity credit
        vol = dict((await db.execute(
            select(EngagementAction.target_author, func.count())
            .where(EngagementAction.status == "acted")
            .group_by(EngagementAction.target_author))).all())

        cands = (await db.execute(select(EngagementCandidate))).scalars().all()
        bumped = {"high": 0, "medium": 0, "low": 0}
        for c in cands:
            engaged = vol.get(c.author_handle, 0)
            if c.in_crm or engaged >= 2:
                tier = "high"
            elif c.is_connection or c.is_followed:
                tier = "medium"
            else:
                tier = "low"
            if c.connection_tier != tier:
                c.connection_tier = tier
                bumped[tier] += 1
        await db.commit()
        logger.info("[tiers] %s", bumped)
        return bumped
