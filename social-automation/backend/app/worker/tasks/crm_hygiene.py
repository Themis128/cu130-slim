"""Celery task — weekly EspoCRM hygiene sweep (dedupe/quarantine/SLA)."""

import logging

from celery import shared_task

from app.services import crm_hygiene
from app.worker._async import run_async

celery_app = __import__("app.worker.celery_app", fromlist=["celery_app"]).celery_app

celery_app.set_default()
celery_app.set_current()

logger = logging.getLogger(__name__)


@shared_task
def run_crm_hygiene() -> None:
    """Monday morning sweep — clean CRM before the week's campaigns run."""
    if not crm_hygiene.crm_hygiene_enabled():
        return
    try:
        run_async(crm_hygiene.run_crm_hygiene())
    except Exception as exc:
        logger.warning("crm hygiene failed: %s", exc)
