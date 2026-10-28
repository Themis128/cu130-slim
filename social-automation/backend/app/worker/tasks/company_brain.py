"""Celery task — write the daily company-brain journal (memory + NC note)."""

import logging

from celery import shared_task
from sqlalchemy import select

from app.models.user import Team
from app.services import company_brain
from app.worker._async import get_session_factory, run_async

celery_app = __import__("app.worker.celery_app", fromlist=["celery_app"]).celery_app

celery_app.set_default()
celery_app.set_current()

logger = logging.getLogger(__name__)


async def _run() -> dict:
    async with get_session_factory()() as db:
        teams = (await db.execute(select(Team))).scalars().all()
        results = [await company_brain.write_daily_journal(db, team) for team in teams]
    logger.info("company brain journal: %s", results)
    return {"teams": len(results), "journals": results}


@shared_task
def write_company_brain_journal() -> None:
    """Nightly: persist the day's real outcomes into the team brain."""
    if not company_brain.company_brain_enabled():
        return
    try:
        run_async(_run())
    except Exception as exc:
        logger.warning("company brain journal failed: %s", exc)
