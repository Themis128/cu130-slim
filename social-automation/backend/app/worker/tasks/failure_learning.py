"""Celery task — failure-trajectory learning sweep.

Recurring publish-failure signatures → remediation proposals (Slack +
audit_logs). The closed learning loop: post_targets.error_message is the
trajectory source; signatures that recur past the threshold escalate as
proposals instead of repeating the same digest alert.
"""

import logging

from celery import shared_task
from sqlalchemy import select

from app.models.user import Team
from app.services import failure_learning
from app.worker._async import get_session_factory, run_async

celery_app = __import__("app.worker.celery_app", fromlist=["celery_app"]).celery_app

celery_app.set_default()
celery_app.set_current()

logger = logging.getLogger(__name__)


@shared_task
def run_failure_learning_sweep() -> dict:
    """Daily sweep — classify failed targets, propose fixes for recurring signatures."""
    try:
        return run_async(_run())
    except Exception as exc:
        logger.warning("failure learning sweep failed: %s", exc)
        return {"error": str(exc)}


async def _run() -> dict:
    async with get_session_factory()() as db:
        teams = (await db.execute(select(Team))).scalars().all()
        results = []
        for team in teams:
            results.append(await failure_learning.run_failure_learning(db, team.id))
        await db.commit()
    return {"teams": len(teams), "reports": results}
