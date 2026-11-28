"""Skool classroom sync beat — hourly diff + alert on new content."""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="app.worker.tasks.skool_sync.sync_skool", bind=True, max_retries=0)
def sync_skool(self):
    from app.worker._async import run_async

    return run_async(_run())


async def _run():
    from app.services import skool_sync
    from app.worker._async import task_session

    diff = getattr(skool_sync, "diff_and_report", None)
    if diff is None:
        logger.warning("[skool] sync_skool: diff_and_report not implemented in app.services.skool_sync — skipped")
        return {"skipped": "diff_and_report not implemented"}
    async with task_session() as db:
        res = await diff(db)
    logger.info("[skool] %s", res)
    return res
