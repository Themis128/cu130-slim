"""Celery task: Talk-bridge sweep — map rooms + post inbound messages.

Runs every TALK_BRIDGE_POLL_S seconds (default 180). New external DM
threads get a per-contact Talk room; new inbound platform messages are
posted into their mapped rooms. Outbound routing is webhook-driven by
the Talk bot endpoint, not this task.
"""

from __future__ import annotations

from typing import Any

from celery import shared_task

from app.services.talk_bridge import bridge_enabled, inbound_sync, sync_rooms
from app.worker._async import run_async
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()


@shared_task(name="app.worker.tasks.talk_bridge.talk_bridge_sync")
def talk_bridge_sync() -> dict[str, Any]:
    return run_async(_run())


async def _run() -> dict[str, Any]:
    if not bridge_enabled():
        return {"skipped": "talk bridge disabled"}

    from sqlalchemy import select

    from app.models.user import Team
    from app.worker._async import task_session

    async with task_session() as db:
        teams = (await db.execute(select(Team.id))).scalars().all()

    results = []
    for team_id in teams:
        try:
            rooms = await sync_rooms(team_id)
            inbound = await inbound_sync(team_id)
            results.append({"team_id": str(team_id), **rooms, **inbound})
        except Exception as exc:  # noqa: BLE001 — one team must not kill the sweep
            results.append({"team_id": str(team_id), "error": str(exc)})
    return {"teams": len(teams), "results": results}
