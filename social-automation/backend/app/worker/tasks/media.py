"""Celery tasks for the media library."""
import asyncio
import logging
import uuid

from celery import shared_task

from app.services import media_ai
from app.services.db_sync import sync_after_worker_task

celery_app = __import__("app.worker.celery_app", fromlist=["celery_app"]).celery_app

logger = logging.getLogger(__name__)


celery_app.set_default()
celery_app.set_current()


@shared_task
def auto_tag_asset_task(asset_id: str) -> None:
    """Run AI auto-tagging for a media asset."""
    try:
        asset_uuid = uuid.UUID(asset_id)
    except ValueError:
        logger.warning("auto_tag_asset_task: invalid asset_id %s", asset_id)
        return

    try:
        asyncio.run(media_ai.auto_tag_asset(asset_uuid))
        # Push worker writes (media_assets) to D1 primary
        asyncio.run(sync_after_worker_task(["media_assets"]))
    except Exception as exc:
        logger.warning("auto_tag_asset_task failed for %s: %s", asset_id, exc)
