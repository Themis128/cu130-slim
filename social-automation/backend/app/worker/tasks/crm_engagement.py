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
    ``Reply received`` note, and bump replied leads to In Process.

    A matched reply is a re-score trigger: the lifecycle sweep's
    ``_needs_score`` re-scores leads whose newest reply post-dates their
    last score note, so enqueue it now instead of waiting for the daily
    08:50 beat — hot leads convert to Contact+Opportunity within the hour.
    """
    try:
        result = run_async(crm_engagement.run_crm_engagement_sweep(dry_run=dry_run))
    except Exception as exc:  # noqa: BLE001
        logger.warning("crm engagement sweep failed: %s", exc)
        return None
    if result and result.get("matched") and not dry_run:
        try:
            from app.worker.tasks.crm_lifecycle import run_crm_lifecycle_sweep

            run_crm_lifecycle_sweep.delay()
        except Exception:  # noqa: BLE001 — never let the trigger break the sweep
            logger.exception("crm engagement: lifecycle trigger failed")
    return result
