"""Celery task for the Telegram channel agent (daily autopilot sweep)."""

from __future__ import annotations

import logging

from app.services.telegram_agent import run_agent_sweep
from app.worker._async import run_async
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.worker.tasks.telegram_agent.run_channel_agent_daily")
def run_channel_agent_daily() -> dict:
    """Daily channel autopilot: plan → generate → publish → poll → adapt.

    Runs at 11:00 (the cadence the retired n8n workflow used). The service
    is idempotent per calendar day and no-ops on non-posting weekdays.
    """
    return run_async(run_agent_sweep())
