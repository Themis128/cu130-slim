"""Celery task: periodic unified-inbox snapshot → inbox_snapshots table.

Runs every 6h so reports always have fresh conversation state to read;
the daily lead-pulse report also captures inline for maximum freshness.
"""

from __future__ import annotations

from typing import Any

from celery import shared_task

from app.worker._async import run_async
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()


@shared_task(name="app.worker.tasks.inbox_snapshot.run_inbox_snapshot")
def run_inbox_snapshot() -> dict[str, Any]:
    return run_async(_run())


async def _run() -> dict[str, Any]:
    from sqlalchemy import select

    from app.models.user import Team
    from app.services.inbox_snapshot import capture
    from app.worker._async import task_session

    async with task_session() as db:
        teams = (await db.execute(select(Team.id))).scalars().all()
        results = []
        for team_id in teams:
            try:
                results.append(await capture(db, team_id))
            except Exception as exc:  # noqa: BLE001 — one team must not kill the sweep
                results.append({"team_id": str(team_id), "error": str(exc)})
        return {"teams": len(teams), "results": results}
