"""Celery task — weekly edit-diff voice learning sweep.

Diffs generated-vs-kept copy on edited posts and distills recurring human
corrections into voice_signature suggestions (audit_logs + Slack; human
stays the approval gate).
"""

import logging

from celery import shared_task
from sqlalchemy import select

from app.models.user import Team
from app.services import edit_diff_learning
from app.worker._async import get_session_factory, run_async

celery_app = __import__("app.worker.celery_app", fromlist=["celery_app"]).celery_app

celery_app.set_default()
celery_app.set_current()

logger = logging.getLogger(__name__)


@shared_task
def run_edit_diff_sweep() -> dict:
    """Sunday sweep — distill the week's edits into voice suggestions."""
    try:
        return run_async(_run())
    except Exception as exc:
        logger.warning("edit diff sweep failed: %s", exc)
        return {"error": str(exc)}


async def _run() -> dict:
    async with get_session_factory()() as db:
        teams = (await db.execute(select(Team))).scalars().all()
        results = [await edit_diff_learning.run_edit_diff_sweep(db, team.id) for team in teams]
        await db.commit()
    return {"teams": len(teams), "reports": results}
