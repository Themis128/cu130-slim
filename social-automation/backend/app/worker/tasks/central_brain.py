"""central-brain — daily decision sweep.

Collects channel health, 7d engagement, follower counts, and hot trend
signals into a BrainSnapshot, decides the day's prioritized actions,
journals the plan to company_brain, and dispatches post decisions through
``schedule_report_actions`` (source="central-brain"). Full-auto per owner
directive; health overrides never generate posts.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.models.user import Team
from app.services.central_brain import run_for_team
from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.worker.tasks.central_brain.run_central_brain")
def run_central_brain() -> dict:
    """Daily: snapshot → decide → journal → dispatch, per team."""
    return run_async(_run())


async def _run() -> dict:
    results: list[dict] = []
    async with task_session() as db:
        teams = (await db.execute(select(Team))).scalars().all()
        for team in teams:
            try:
                results.append(await run_for_team(db, team))
            except Exception as exc:  # noqa: BLE001 — one team must not kill the sweep
                logger.warning("central_brain failed for team %s: %s", team.id, str(exc)[:200])
                results.append({"team_id": str(team.id), "status": "failed", "reason": str(exc)[:160]})
        await db.commit()
    return {"teams": len(results), "results": results}
