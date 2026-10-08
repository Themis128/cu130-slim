"""Celery task — mirror media assets to the omv Nextcloud workspace."""

import logging
import uuid

from celery import shared_task

from app.services import nextcloud_export
from app.worker._async import get_session_factory, run_async

celery_app = __import__("app.worker.celery_app", fromlist=["celery_app"]).celery_app

celery_app.set_default()
celery_app.set_current()

logger = logging.getLogger(__name__)


@shared_task
def export_media_to_nextcloud(asset_id: str) -> None:
    """Upload one media asset to Nextcloud WebDAV (fire-and-forget)."""
    if not nextcloud_export.nextcloud_export_enabled():
        return
    try:
        asset_uuid = uuid.UUID(asset_id)
    except ValueError:
        logger.warning("nextcloud export: invalid asset_id %s", asset_id)
        return

    async def _run() -> None:
        # NullPool: run_async() creates a fresh loop per task; pooled
        # connections from the shared engine bind to the creating loop.
        await nextcloud_export.export_media_asset(
            asset_uuid, get_session_factory()
        )

    try:
        run_async(_run())
    except Exception as exc:
        logger.warning("nextcloud export failed for %s: %s", asset_id, exc)
