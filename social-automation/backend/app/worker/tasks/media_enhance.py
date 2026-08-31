"""Celery tasks for batch media AI enhancement."""
import asyncio
import logging
import uuid

from celery import shared_task

from app.services import image_enhance, image_transform, minio_storage, r2_storage
from app.services.db_sync import sync_after_worker_task

celery_app = __import__("app.worker.celery_app", fromlist=["celery_app"]).celery_app

logger = logging.getLogger(__name__)

celery_app.set_default()
celery_app.set_current()

UPLOAD_DIR = "/app/uploads"


async def _load_asset_bytes(asset) -> bytes:
    """Load image bytes from any storage backend."""
    if asset.storage_backend == "r2":
        return await r2_storage.get_object(asset.storage_path)
    if asset.storage_backend == "minio":
        return await minio_storage.get_object(asset.storage_path)
    from app.core.path_utils import safe_resolve
    return safe_resolve(UPLOAD_DIR, asset.storage_path).read_bytes()


async def _run_batch(asset_ids: list[str], operation: str, params: dict) -> None:
    """Execute batch enhancement operation on multiple assets."""
    from sqlalchemy import select

    from app.db.session import async_session_maker
    from app.models.content import MediaAsset

    async with async_session_maker() as db:
        for asset_id_str in asset_ids:
            try:
                asset_id = uuid.UUID(asset_id_str)
                result = await db.execute(select(MediaAsset).where(MediaAsset.id == asset_id))
                asset = result.scalar_one_or_none()
                if not asset:
                    logger.warning("Batch: asset %s not found", asset_id_str)
                    continue

                image_bytes = await _load_asset_bytes(asset)
                out_bytes = None
                mime_type = "image/jpeg"

                if operation == "resize":
                    r = image_transform.resize_image(image_bytes, **params)
                    out_bytes = r.image_bytes
                    mime_type = r.mime_type
                elif operation == "convert":
                    r = image_transform.convert_format(image_bytes, **params)
                    out_bytes = r.image_bytes
                    mime_type = r.mime_type
                elif operation == "compress":
                    r = image_transform.compress_image(image_bytes, **params)
                    out_bytes = r.image_bytes
                    mime_type = r.mime_type
                elif operation == "upscale":
                    out_bytes, mime_type, w, h = image_enhance.upscale_image(image_bytes, **params)
                elif operation == "remove_bg":
                    out_bytes, mime_type = await image_enhance.remove_background(image_bytes)
                elif operation == "smart_crop":
                    use_ai = params.pop("use_ai", True)
                    if use_ai:
                        out_bytes, mime_type, w, h = await image_enhance.smart_crop_async(image_bytes, **params)
                    else:
                        out_bytes, mime_type, w, h = image_enhance.smart_crop(image_bytes, **params)
                elif operation == "alt_text":
                    alt_text = await image_enhance.generate_alt_text(image_bytes)
                    if alt_text:
                        asset.alt_text = alt_text
                        await db.commit()
                    continue
                else:
                    logger.warning("Batch: unknown operation %s", operation)
                    continue

                if out_bytes:
                    # Store result as a new media asset
                    from app.services.media_storage import persist_generated_image
                    # Determine extension from mime type
                    ext_map = {
                        "image/png": ".png",
                        "image/jpeg": ".jpg",
                        "image/webp": ".webp",
                        "image/avif": ".avif",
                    }
                    ext = ext_map.get(mime_type, ".png")
                    await persist_generated_image(
                        db,
                        team_id=asset.team_id,
                        user_id=asset.user_id,
                        image_bytes=out_bytes,
                        prompt=f"batch_{operation}",
                        source="ai-enhanced",
                        extension=ext,
                    )

            except Exception as exc:
                logger.warning("Batch: failed to process asset %s: %s", asset_id_str, exc)

        # Sync to D1
        await sync_after_worker_task(["media_assets"])


@shared_task
def batch_enhance_task(asset_ids: list[str], operation: str, params: dict) -> None:
    """Run a batch enhancement operation on multiple media assets."""
    try:
        asyncio.run(_run_batch(asset_ids, operation, params))
    except Exception as exc:
        logger.warning("batch_enhance_task failed: %s", exc)
