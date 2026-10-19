"""Celery task — CRM engagement sweep (inbound reply → note + status)."""

import logging

from celery import shared_task

from app.services import crm_engagement
from app.worker._async import run_async

celery_app = __import__("app.worker.celery_app", fromlist=["celery_app"]).celery_app

celery_app.set_default()
celery_app.set_current()

logger = logging.getLogger(__name__)


@shared_task
def run_crm_engagement_sweep(dry_run: bool = False) -> dict | None:
    """Match inbound mailbox replies to CRM leads/contacts, attach a
    ``Reply received`` note, and bump replied leads to In Process."""
    try:
        return run_async(crm_engagement.run_crm_engagement_sweep(dry_run=dry_run))
    except Exception as exc:  # noqa: BLE001
        logger.warning("crm engagement sweep failed: %s", exc)
        return None
