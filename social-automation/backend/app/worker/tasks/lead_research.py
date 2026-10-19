"""Celery task — daily lead-diligence sweep (the Sales dept's research agent)."""

import logging

from celery import shared_task

from app.services import lead_research
from app.worker._async import run_async

celery_app = __import__("app.worker.celery_app", fromlist=["celery_app"]).celery_app

celery_app.set_default()
celery_app.set_current()

logger = logging.getLogger(__name__)


@shared_task
def run_lead_research_sweep(dry_run: bool = False) -> dict | None:
    """Attach a diligence note (website summary + Wikipedia + CRM context) to
    every eligible EspoCRM lead that doesn't have one yet. The note itself is
    the idempotency marker — re-runs skip researched leads."""
    from app.core.config import get_settings

    if not get_settings().LEAD_RESEARCH_ENABLED:
        return None
    try:
        return run_async(lead_research.run_lead_research(dry_run=dry_run))
    except Exception as exc:  # noqa: BLE001
        logger.warning("lead research sweep failed: %s", exc)
        return None
