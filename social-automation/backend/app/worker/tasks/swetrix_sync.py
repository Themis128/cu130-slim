"""swetrix-sync — pull Swetrix CE site-traffic aggregates into Postgres.

Runs 5min before export-datalake so the fresh ``site_traffic_daily`` rollup
lands in ``lake/site-traffic/`` on the same export pass.
"""

from __future__ import annotations

from celery import shared_task

from app.services import swetrix_sync
from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()


@shared_task(name="app.worker.tasks.swetrix_sync.sync_swetrix")
def sync_swetrix() -> dict:
    """Sweep every configured Swetrix project into ``site_traffic_daily``."""

    async def _run() -> dict:
        async with task_session() as db:
            return await swetrix_sync.sync_all_swetrix(db)

    return run_async(_run())
