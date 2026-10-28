"""Celery task: daily monetization report → email + auto-scheduled actions."""

from __future__ import annotations

from typing import Any

from celery import shared_task

from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()

_worker_db = task_session


@shared_task(name="app.worker.tasks.monetization.run_daily_monetization_report")
def run_daily_monetization_report(send: bool = True, execute: bool = True, insight_days: int = 30) -> dict[str, Any]:
    return run_async(_run(send=send, execute=execute, insight_days=insight_days))


async def _run(*, send: bool, execute: bool, insight_days: int) -> dict[str, Any]:
    from app.services.monetization_report import run_monetization_report_for_all_teams

    async with _worker_db() as db:
        results = await run_monetization_report_for_all_teams(db, insight_days=insight_days, send=send, execute=execute)
        return {"reports": results}
