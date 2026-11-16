"""datalake_rewards sweep — score executed actions against post metrics.

Runs after the executor (same 6h cadence, +50min offset): joins
``datalake_actions`` events to ``post_analytics_snapshots``, persists
``datalake_rewards`` events, and refreshes the action weights that
``pick_actions`` consults on the next insights sweep. Closes the
explore→log→learn loop for datalake-driven actions.
"""

from __future__ import annotations

from celery import shared_task
from sqlalchemy import select

from app.models.user import Team
from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()


@shared_task(name="app.worker.tasks.datalake_rewards.datalake_reward_sweep")
def datalake_reward_sweep() -> dict:
    """Score un-scored action events per team; persist reward events."""
    from app.services import datalake_rewards as dr

    async def _run() -> dict:
        async with task_session() as db:
            teams = (await db.execute(select(Team.id))).scalars().all()
            out = []
            for tid in teams:
                res = await dr.score_actions(db, tid)
                weights = await dr.action_weights(db, tid)
                out.append({"team": str(tid), **res, "weighted_types": len(weights)})
            return {"teams": len(out), "details": out}

    return run_async(_run())
