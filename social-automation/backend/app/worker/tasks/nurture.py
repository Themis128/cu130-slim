"""Celery task — daily public-lead nurture sweep."""

import logging

from celery import shared_task

from app.worker._async import run_async

celery_app = __import__("app.worker.celery_app", fromlist=["celery_app"]).celery_app
celery_app.set_default()
celery_app.set_current()

logger = logging.getLogger(__name__)


async def _run() -> dict | None:
    import uuid

    from app.core.config import get_settings

    team_raw = getattr(get_settings(), "CLOUDLESS_WEB_ANALYTICS_TEAM_ID", "")
    if not team_raw:
        return {"skipped": "no public-lead team configured"}

    from app.services.nurture_sequence import nurture_sweep
    from app.worker._async import task_session

    async with task_session() as db:
        return await nurture_sweep(db, uuid.UUID(team_raw))


@shared_task
def run_nurture_sweep() -> dict | None:
    """Daily — advance public leads through the 3-touch nurture sequence."""
    try:
        return run_async(_run())
    except Exception as exc:  # noqa: BLE001
        logger.warning("nurture sweep failed: %s", exc)
        return None
