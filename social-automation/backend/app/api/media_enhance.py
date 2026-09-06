"""Media AI enhancement API — background removal, upscaling, smart crop, quality scoring, alt text, format conversion, platform presets."""
import io
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.db.session import get_db
from app.models.content import MediaAsset, StorageBackend
from app.models.user import Team, TeamMember, User
from app.services import minio_storage, r2_storage
from app.services.image_enhance import (
    generate_alt_text,
    remove_background,
    score_image_quality,
    smart_crop,
    smart_crop_async,
    upscale_image,
)
from app.services.image_transform import (
    PLATFORM_PRESETS,
    add_watermark,
    compress_image,
    convert_format,
    crop_image,
    get_image_info,
    resize_image,
)

router = APIRouter()
logger = logging.getLogger(__name__)

UPLOAD_DIR = "/app/uploads"


async def _load_asset_bytes(asset: MediaAsset) -> bytes:
    """Load image bytes from any storage backend."""
    if asset.storage_backend == StorageBackend.r2:
        return await r2_storage.get_object(asset.storage_path)
    if asset.storage_backend == StorageBackend.minio:
        return await minio_storage.get_object(asset.storage_path)
    from app.core.path_utils import safe_resolve
    return safe_resolve(UPLOAD_DIR, asset.storage_path).read_bytes()


async def _get_asset(asset_id: uuid.UUID, user: User, db: AsyncSession) -> MediaAsset:
    """Get a media asset owned by the user's team."""
    result = await db.execute(
        select(MediaAsset)
        .join(Team, MediaAsset.team_id == Team.id)
        .join(TeamMember, Team.id == TeamMember.team_id)
        .where(MediaAsset.id == asset_id, TeamMember.user_id == user.id)
    )
    asset = result.scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=404, detail="Media asset not found")
    return asset


def _stream_response(image_bytes: bytes, mime_type: str) -> StreamingResponse:
    """Return image bytes as a streaming response."""
    return StreamingResponse(io.BytesIO(image_bytes), media_type=mime_type)


# ── Schemas ───────────────────────────────────────────────────────────────────

class ResizeRequest(BaseModel):
    preset: str | None = None
    width: int | None = None
    height: int | None = None
    fit: str = "cover"  # cover, contain
    format: str = "jpeg"
    quality: int = 85


class CropRequest(BaseModel):
    x: int
    y: int
    width: int
    height: int
    format: str = "jpeg"
    quality: int = 85


class ConvertFormatRequest(BaseModel):
    format: str = "webp"
    quality: int = 85


class CompressRequest(BaseModel):
    target_size_kb: int = 500
    format: str = "jpeg"
    min_quality: int = 30


class WatermarkRequest(BaseModel):
    text: str
    position: str = "bottom-right"
    opacity: int = 128
    font_size: int = 36
    color: list[int] = [255, 255, 255]
    format: str = "jpeg"
    quality: int = 85


class UpscaleRequest(BaseModel):
    scale: int = 2  # 2 or 4


class SmartCropRequest(BaseModel):
    target_width: int
    target_height: int
    use_ai: bool = True


class QualityScoreResponse(BaseModel):
    overall: int
    sharpness: int
    brightness: int
    contrast: int
    blur_detected: bool
    too_dark: bool
    too_bright: bool
    issues: list[str]


class AltTextResponse(BaseModel):
    alt_text: str


class ImageInfoResponse(BaseModel):
    width: int
    height: int
    mode: str
    format: str


class PresetsResponse(BaseModel):
    presets: dict[str, dict]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/presets", response_model=PresetsResponse)
async def list_presets(
    current_user: User = Depends(get_current_user),
):
    """List all available platform-specific resize presets."""
    return {"presets": PLATFORM_PRESETS}


@router.get("/assets/{asset_id}/info", response_model=ImageInfoResponse)
async def get_asset_info(
    asset_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get image dimensions and format info."""
    asset = await _get_asset(asset_id, current_user, db)
    image_bytes = await _load_asset_bytes(asset)
    info = get_image_info(image_bytes)
    return info


@router.get("/assets/{asset_id}/quality", response_model=QualityScoreResponse)
async def get_quality_score(
    asset_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Score image quality (blur, brightness, contrast). Local computation, no AI."""
    asset = await _get_asset(asset_id, current_user, db)
    image_bytes = await _load_asset_bytes(asset)
    score = score_image_quality(image_bytes)
    return score


@router.post("/assets/{asset_id}/resize")
async def resize_asset(
    asset_id: uuid.UUID,
    body: ResizeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Resize an image to a platform preset or custom dimensions."""
    asset = await _get_asset(asset_id, current_user, db)
    image_bytes = await _load_asset_bytes(asset)
    try:
        result = resize_image(
            image_bytes,
            preset=body.preset,
            width=body.width,
            height=body.height,
            fit=body.fit,
            format=body.format,
            quality=body.quality,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _stream_response(result.image_bytes, result.mime_type)


@router.post("/assets/{asset_id}/crop")
async def crop_asset(
    asset_id: uuid.UUID,
    body: CropRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Crop an image to a specific region."""
    asset = await _get_asset(asset_id, current_user, db)
    image_bytes = await _load_asset_bytes(asset)
    result = crop_image(image_bytes, body.x, body.y, body.width, body.height, body.format, body.quality)
    return _stream_response(result.image_bytes, result.mime_type)


@router.post("/assets/{asset_id}/convert")
async def convert_asset_format(
    asset_id: uuid.UUID,
    body: ConvertFormatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Convert image to a different format (jpeg, png, webp, avif)."""
    asset = await _get_asset(asset_id, current_user, db)
    image_bytes = await _load_asset_bytes(asset)
    result = convert_format(image_bytes, body.format, body.quality)
    return _stream_response(result.image_bytes, result.mime_type)


@router.post("/assets/{asset_id}/compress")
async def compress_asset(
    asset_id: uuid.UUID,
    body: CompressRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Compress image to target file size."""
    asset = await _get_asset(asset_id, current_user, db)
    image_bytes = await _load_asset_bytes(asset)
    result = compress_image(image_bytes, body.target_size_kb, body.format, body.min_quality)
    return _stream_response(result.image_bytes, result.mime_type)


@router.post("/assets/{asset_id}/watermark")
async def watermark_asset(
    asset_id: uuid.UUID,
    body: WatermarkRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add a text watermark to an image."""
    asset = await _get_asset(asset_id, current_user, db)
    image_bytes = await _load_asset_bytes(asset)
    result = add_watermark(
        image_bytes,
        text=body.text,
        position=body.position,
        opacity=body.opacity,
        font_size=body.font_size,
        color=tuple(body.color),
        format=body.format,
        quality=body.quality,
    )
    return _stream_response(result.image_bytes, result.mime_type)


@router.post("/assets/{asset_id}/upscale")
async def upscale_asset(
    asset_id: uuid.UUID,
    body: UpscaleRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upscale image 2x or 4x using LANCZOS + sharpening."""
    asset = await _get_asset(asset_id, current_user, db)
    image_bytes = await _load_asset_bytes(asset)
    try:
        out_bytes, mime, w, h = upscale_image(image_bytes, body.scale)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _stream_response(out_bytes, mime)


@router.post("/assets/{asset_id}/remove-background")
async def remove_bg_asset(
    asset_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove image background using AI (Cloudflare Workers AI or local rembg)."""
    asset = await _get_asset(asset_id, current_user, db)
    image_bytes = await _load_asset_bytes(asset)
    try:
        out_bytes, mime = await remove_background(image_bytes)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _stream_response(out_bytes, mime)


@router.post("/assets/{asset_id}/smart-crop")
async def smart_crop_asset(
    asset_id: uuid.UUID,
    body: SmartCropRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Smart crop image to target dimensions, focusing on the main subject."""
    asset = await _get_asset(asset_id, current_user, db)
    image_bytes = await _load_asset_bytes(asset)
    if body.use_ai:
        out_bytes, mime, w, h = await smart_crop_async(image_bytes, body.target_width, body.target_height)
    else:
        out_bytes, mime, w, h = smart_crop(image_bytes, body.target_width, body.target_height)
    return _stream_response(out_bytes, mime)


@router.post("/assets/{asset_id}/alt-text", response_model=AltTextResponse)
async def generate_alt_text_endpoint(
    asset_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate accessibility-focused alt text using AI vision model.

    The AI-generated alt text is spellchecked via LanguageTool before return.
    """
    asset = await _get_asset(asset_id, current_user, db)
    image_bytes = await _load_asset_bytes(asset)
    alt_text = await generate_alt_text(image_bytes)
    if not alt_text:
        raise HTTPException(status_code=503, detail="Alt text generation unavailable — AI services not configured")
    # Spellcheck the AI-generated alt text before returning.
    from app.services.media_spellcheck import correct_text
    corrected = await correct_text(alt_text)
    return {"alt_text": corrected or alt_text}


# ── Batch operations ──────────────────────────────────────────────────────────

class BatchEnhanceRequest(BaseModel):
    asset_ids: list[uuid.UUID]
    operation: str  # resize, convert, compress, upscale, remove_bg, smart_crop
    params: dict = {}


class BatchEnhanceResponse(BaseModel):
    task_id: str
    status: str
    asset_count: int


@router.post("/batch", response_model=BatchEnhanceResponse)
async def batch_enhance(
    body: BatchEnhanceRequest,
    current_user: User = Depends(get_current_user),
):
    """Queue a batch enhancement operation as a Celery task."""
    from app.worker.tasks.media_enhance import batch_enhance_task

    if not body.asset_ids:
        raise HTTPException(status_code=400, detail="No assets specified")
    if len(body.asset_ids) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 assets per batch")

    task = batch_enhance_task.delay(
        [str(aid) for aid in body.asset_ids],
        body.operation,
        body.params,
    )
    return {"task_id": task.id, "status": "queued", "asset_count": len(body.asset_ids)}
