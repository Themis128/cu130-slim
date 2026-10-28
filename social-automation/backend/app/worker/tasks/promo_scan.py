"""Celery task: daily free-promotion scan → email report."""
from __future__ import annotations

from typing import Any

from celery import shared_task

from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()

_worker_db = task_session


@shared_task(name="app.worker.tasks.promo_scan.run_promo_scan")
def run_promo_scan(send: bool = True) -> dict[str, Any]:
    return run_async(_run(send=send))


async def _run(*, send: bool) -> dict[str, Any]:
    from app.services.promo_scan import run_promo_scan as _scan

    async with _worker_db() as db:
        return await _scan(db, send=send)
