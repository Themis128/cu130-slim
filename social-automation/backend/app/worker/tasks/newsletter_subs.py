"""Celery task — weekly LinkedIn newsletter subscriber → CRM sync."""

import logging

from celery import shared_task

from app.worker._async import run_async

celery_app = __import__("app.worker.celery_app", fromlist=["celery_app"]).celery_app
celery_app.set_default()
celery_app.set_current()

logger = logging.getLogger(__name__)


async def _run() -> dict | None:
    from app.core.config import get_settings
    from app.services.newsletter_subscribers import sync_newsletter_subscribers

    urn = getattr(get_settings(), "LINKEDIN_NEWSLETTER_SERIES_URN", "")
    return await sync_newsletter_subscribers(urn)


@shared_task
def run_newsletter_subscriber_sync() -> dict | None:
    """Weekly — pull newsletter subscribers into EspoCRM via the sidecar."""
    try:
        return run_async(_run())
    except Exception as exc:  # noqa: BLE001
        logger.warning("newsletter subscriber sync failed: %s", exc)
        return None
