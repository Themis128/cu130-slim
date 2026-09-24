"""Celery task: LinkedIn Ads daily report → Slack ads channel + email."""
from __future__ import annotations

import asyncio
from typing import Any

from celery import shared_task

from app.services.linkedin_ads_report import run_daily_report
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()


@shared_task(name="app.worker.tasks.linkedin_ads_report.send_linkedin_ads_report")
def send_linkedin_ads_report() -> dict[str, Any]:
    """Collect Campaign Manager metrics and deliver the daily report.

    Runs at 10:00 Europe/Athens (beat) until LINKEDIN_ADS_END_DATE — the last
    report is marked final and the task skips itself afterwards.
    """
    return asyncio.run(run_daily_report())
