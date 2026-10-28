"""LinkedIn Ads snapshot — pulls account+campaign state hourly."""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="app.worker.tasks.linkedin_ads_sync.sync_linkedin_ads", bind=True, max_retries=0)
def sync_linkedin_ads(self):
    from app.worker._async import run_async

    return run_async(_run())


async def _run():
    from app.db.session import async_session_maker
    from app.services import linkedin_ads

    async with async_session_maker() as db:
        snap = await linkedin_ads.snapshot(db)
        await db.commit()
    logger.info("[li-ads] %s", snap)
    return snap
