"""Celery task — sync SocialAuto metrics + pipeline into Nextcloud Tables/Deck."""

import logging

from celery import shared_task

from app.services import nextcloud_sync
from app.worker._async import get_session_factory, run_async

celery_app = __import__("app.worker.celery_app", fromlist=["celery_app"]).celery_app

celery_app.set_default()
celery_app.set_current()

logger = logging.getLogger(__name__)


@shared_task
def sync_nextcloud_surfaces() -> None:
    """Push follower metrics → Tables and post pipeline → Deck (idempotent)."""
    if not nextcloud_sync.nextcloud_sync_enabled():
        return
    try:
        run_async(nextcloud_sync.sync_all(get_session_factory()))
    except Exception as exc:
        logger.warning("nextcloud sync failed: %s", exc)
