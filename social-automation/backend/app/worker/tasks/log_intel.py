"""log_intel beat — syslog→LLM triage digest (Shift.Forward #4).

Every 30 min: pull Loki errors/warnings → loop-engineered triage →
Slack digest. The syslog→vector→LLM pattern on existing infra.
"""
from __future__ import annotations

import logging

from celery import shared_task

from app.services.log_intel import triage_logs
from app.services.slack_notifications import post_alert_to_slack
from app.worker._async import run_async
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()
logger = logging.getLogger(__name__)


@celery_app.task(name="app.worker.tasks.log_intel.digest")
def digest() -> dict:
    async def _go():
        r = await triage_logs(minutes=30)
        if r["lines"]:
            await post_alert_to_slack(
                f":mag: *log triage* ({r['lines']} lines/30m)\n{r['summary']}")
        return r
    return run_async(_go())
