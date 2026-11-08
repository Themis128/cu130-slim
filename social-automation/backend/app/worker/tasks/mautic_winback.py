"""Celery task — daily Mautic win-back sweep for cold EspoCRM leads."""

import logging

from celery import shared_task

from app.services import mautic_winback
from app.worker._async import run_async

logger = logging.getLogger(__name__)


@shared_task
def run_mautic_winback_sweep(dry_run: bool = False) -> dict | None:
    """Advance cold leads through the Day 0 / +5 / +14 re-engagement
    sequence; engaged leads hand off to the lifecycle scorer."""
    try:
        return run_async(mautic_winback.run_mautic_winback_sweep(dry_run=dry_run))
    except Exception as exc:  # noqa: BLE001
        logger.warning("mautic winback sweep failed: %s", exc)
        return None
