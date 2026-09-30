"""Celery tasks for the media library."""
import asyncio
import logging
import uuid

from celery import shared_task
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
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

    async def _run() -> None:
        # NullPool: asyncio.run() creates a fresh loop per task; pooled
        # connections from the shared engine bind to the creating loop.
        engine = create_async_engine(get_settings().DATABASE_URL, poolclass=NullPool)
        try:
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            await media_ai.auto_tag_asset(asset_uuid, session_factory=factory)
        finally:
            await engine.dispose()

    try:
        asyncio.run(_run())
        # Push worker writes (media_assets) to D1 primary
        asyncio.run(sync_after_worker_task(["media_assets"]))
    except Exception as exc:
        logger.warning("auto_tag_asset_task failed for %s: %s", asset_id, exc)
