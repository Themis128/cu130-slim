"""devapps-sync — pull developer-app data (health, insights, demographics,
quota) for every connected account and persist devapp_snapshot events.

Runs 5min before export-datalake so the fresh snapshots land in
``lake/socialauto-devapps/`` + ``lake/socialauto-channel-actions/`` on the
same export pass.
"""

from __future__ import annotations

from celery import shared_task

from app.services import devapps_sync
from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()


@shared_task(name="app.worker.tasks.devapps_sync.sync_devapps")
def sync_devapps(platform: str | None = None) -> dict:
    """Sweep all active accounts through their platform developer-app pull."""

    async def _run() -> dict:
        async with task_session() as db:
            return await devapps_sync.sync_all_devapps(db, platform=platform)

    return run_async(_run())
