"""Celery task — daily CRM lifecycle sweep (score + hot-lead convert)."""

import logging

from celery import shared_task

from app.services import crm_lifecycle
from app.worker._async import run_async

celery_app = __import__("app.worker.celery_app", fromlist=["celery_app"]).celery_app

celery_app.set_default()
celery_app.set_current()

logger = logging.getLogger(__name__)


@shared_task
def run_crm_lifecycle_sweep(dry_run: bool = False) -> dict | None:
    """Score every unscored live EspoCRM lead; convert hot real-email leads
    to Contact+Opportunity via Espo's own convert action."""
    try:
        return run_async(crm_lifecycle.run_crm_lifecycle_sweep(dry_run=dry_run))
    except Exception as exc:  # noqa: BLE001
        logger.warning("crm lifecycle sweep failed: %s", exc)
        return None
