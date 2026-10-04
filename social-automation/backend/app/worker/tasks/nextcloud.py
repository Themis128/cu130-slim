"""Celery task — mirror media assets to the omv Nextcloud workspace."""

import asyncio
import logging
import uuid

from celery import shared_task
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.services import nextcloud_export

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
        # NullPool: asyncio.run() creates a fresh loop per task; pooled
        # connections from the shared engine bind to the creating loop.
        engine = create_async_engine(get_settings().DATABASE_URL, poolclass=NullPool)
        try:
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            await nextcloud_export.export_media_asset(asset_uuid, factory)
        finally:
            await engine.dispose()

    try:
        asyncio.run(_run())
    except Exception as exc:
        logger.warning("nextcloud export failed for %s: %s", asset_id, exc)
