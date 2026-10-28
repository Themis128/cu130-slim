"""Email campaign send task — per-recipient delivery with daily budget."""

from __future__ import annotations

import logging
import uuid

from app.worker._async import run_async
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.worker.tasks.email_campaigns.send_campaign",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def send_campaign(self, campaign_id: str) -> dict:
    """Send a queued campaign. Each recipient gets an individual message with
    a one-click unsubscribe header; the daily budget is enforced service-side."""
    from app.services.email_campaigns import send_campaign as _send

    try:
        return run_async(_send(uuid.UUID(campaign_id)))
    except Exception as exc:  # noqa: BLE001
        logger.warning("campaign %s send failed (attempt %s): %s", campaign_id, self.request.retries + 1, exc)
        raise self.retry(exc=exc) from exc
