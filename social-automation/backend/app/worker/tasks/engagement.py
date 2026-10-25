"""Engagement sweep — dispatch queued actions honoring jitter + caps."""
import logging

from celery import shared_task
from sqlalchemy import select

logger = logging.getLogger(__name__)

@shared_task(name="app.worker.tasks.engagement.dispatch_engagements", bind=True)
def dispatch_engagements(self, limit: int = 10):
    from app.worker._async import run_async
    return run_async(_run(limit))

async def _run(limit: int):
    from app.db.session import async_session_maker
    from app.models.engagement import EngagementAction
    async with async_session_maker() as db:
        rows = (await db.execute(select(EngagementAction).where(
            EngagementAction.status == "pending").order_by(
            EngagementAction.score.desc()).limit(limit))).scalars().all()
        done = []
        for r in rows:
            # jitter is honored at dispatch — mark for the platform actor
            r.status = "dispatched"
            done.append(f"{r.platform}:{r.action}@{r.target_author}")
        await db.commit()
        return {"dispatched": done}
