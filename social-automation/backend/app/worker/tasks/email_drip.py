"""Celery task — daily autonomous email drip sweep."""

import logging

from celery import shared_task

from app.services import email_drip
from app.worker._async import run_async

celery_app = __import__("app.worker.celery_app", fromlist=["celery_app"]).celery_app

celery_app.set_default()
celery_app.set_current()

logger = logging.getLogger(__name__)


@shared_task
def run_email_drip_sweep(dry_run: bool = False) -> dict | None:
    """Daily sweep — mail first-approach to new CRM leads and renewal to
    entities entering a renewal window. Per-recipient dedupe inside."""
    if not email_drip.email_drip_enabled():
        return None
    try:
        return run_async(email_drip.run_email_drip_sweep(dry_run=dry_run))
    except Exception as exc:  # noqa: BLE001
        logger.warning("email drip sweep failed: %s", exc)
        return None
