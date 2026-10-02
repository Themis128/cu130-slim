"""Celery tasks for the media library."""
import asyncio
import logging
import uuid

from celery import shared_task
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.services import comfyui_video, media_ai
from app.services.db_sync import sync_after_worker_task
from app.services.media_storage import save_uploaded_media

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


@shared_task
def generate_video_asset_task(team_id: str, user_id: str, prompt: str, options: dict | None = None) -> str:
    """Generate a video via ComfyUI (LTX-Video 2B GGUF) and store a MediaAsset.

    Returns the new media asset id as a string so the API can hand it back
    through the Celery result backend.
    """
    options = options or {}

    async def _run() -> str:
        engine = create_async_engine(get_settings().DATABASE_URL, poolclass=NullPool)
        try:
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as db:
                data, meta = await comfyui_video.generate_video(
                    prompt=prompt,
                    negative_prompt=options.get("negative_prompt"),
                    width=int(options.get("width") or 480),
                    height=int(options.get("height") or 832),
                    num_frames=int(options.get("num_frames") or 41),
                    frame_rate=int(options.get("frame_rate") or 25),
                    steps=int(options.get("steps") or 25),
                    cfg=float(options.get("cfg_scale") or 3.0),
                    seed=options.get("seed"),
                    filename_prefix="socialauto",
                )
                asset = await save_uploaded_media(
                    db,
                    team_id=uuid.UUID(team_id),
                    user_id=uuid.UUID(user_id),
                    original_filename=meta["filename"],
                    content=data,
                    mime_type="video/mp4",
                    alt_text=options.get("alt_text") or f"AI-generated video: {prompt[:120]}",
                    tags=options.get("tags") or ["comfyui", "ltxv", "generated-video"],
                    width=meta["width"],
                    height=meta["height"],
                )
                asset.source = "comfyui-ltxv"
                asset.generation_prompt = prompt
                asset.duration_seconds = int(round(meta["duration_seconds"]))
                await db.commit()
                return str(asset.id)
        finally:
            await engine.dispose()

    asset_id = asyncio.run(_run())
    asyncio.run(sync_after_worker_task(["media_assets"]))
    return asset_id
