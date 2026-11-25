"""Celery task: Talk-bridge sweep — map rooms + post inbound messages.

Runs every TALK_BRIDGE_POLL_S seconds (default 180). New external DM
threads get a per-contact Talk room; new inbound platform messages are
posted into their mapped rooms. Outbound routing is webhook-driven by
the Talk bot endpoint, not this task.
"""

from __future__ import annotations

import time
from typing import Any

from celery import shared_task

from app.services.talk_bridge import bridge_enabled, inbound_sync, sync_rooms
from app.worker._async import run_async

_SYNC_LOCK = "talk_bridge:sync:lock"
_SYNC_LOCK_TTL = 900  # covers the slowest full-fleet sweep (~7 min)


def _lock_client():
    try:
        import redis

        from app.core.config import get_settings

        return redis.Redis.from_url(get_settings().REDIS_URL, decode_responses=True)
    except Exception:  # noqa: BLE001
        return None


@shared_task(name="app.worker.tasks.talk_bridge.talk_bridge_sync")
def talk_bridge_sync() -> dict[str, Any]:
    # Run-in-progress lock collapses beat pile-up: a full 255-team sweep takes
    # ~7 min vs the 180s beat cadence, so queued duplicates would otherwise
    # starve every other task on the shared default/celery queues.
    r = _lock_client()
    token = str(time.time_ns())
    locked = False
    if r is not None:
        try:
            locked = bool(r.set(_SYNC_LOCK, token, nx=True, ex=_SYNC_LOCK_TTL))
            if not locked:
                return {"skipped": "run-in-progress"}
        except Exception:  # noqa: BLE001
            pass
    try:
        return run_async(_run())
    finally:
        if locked:
            try:
                if r.get(_SYNC_LOCK) == token:
                    r.delete(_SYNC_LOCK)
            except Exception:  # noqa: BLE001
                pass


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
            rooms = await sync_rooms(team_id, session_factory=task_session)
            inbound = await inbound_sync(team_id, session_factory=task_session)
            results.append({"team_id": str(team_id), **rooms, **inbound})
        except Exception as exc:  # noqa: BLE001 — one team must not kill the sweep
            results.append({"team_id": str(team_id), "error": str(exc)})
    return {"teams": len(teams), "results": results}
