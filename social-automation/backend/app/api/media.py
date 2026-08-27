import io
import json
import os
import uuid
from datetime import UTC, datetime

import aiofiles
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, Response
from PIL import Image
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.db.session import get_db
from app.models.content import MediaAsset
from app.models.user import Team, TeamMember, User

router = APIRouter()

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/app/uploads")

# Formats every modern browser can render natively inside an <img> tag.
BROWSER_NATIVE_IMAGE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/gif",
    "image/webp",
    "image/svg+xml",
    "image/apng",
    "image/avif",
    "image/bmp",
    "image/x-icon",
    "image/vnd.microsoft.icon",
    "image/heic",  # Safari only, but pass through rather than re-encode
}

# Register Pillow plugins for formats the browser can't display (HEIC/HEIF
# from iPhones, AVIF on older stacks).  Imported defensively: the media API
# keeps working — those formats just fall back to a download prompt — if a
# plugin is unavailable in the environment.
try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # pragma: no cover - environment-dependent
    pass
try:
    import pillow_avif  # noqa: F401  (registers the AVIF codec on import)
except ImportError:  # pragma: no cover - environment-dependent
    pass


class MediaAssetResponse(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID
    filename: str | None
    mime_type: str | None
    size_bytes: int | None
    storage_path: str
    width: int | None
    height: int | None
    duration_seconds: int | None
    alt_text: str | None
    tags: list[str]
    source: str
    generation_prompt: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class MediaListResponse(BaseModel):
    assets: list[MediaAssetResponse]
    total: int
    page: int
    page_size: int


@router.post("/upload", response_model=MediaAssetResponse, status_code=status.HTTP_201_CREATED)
async def upload_media(
    file: UploadFile = File(...),
    alt_text: str = Form(None),
    tags: str = Form(""),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()
    if not team:
        raise HTTPException(status_code=400, detail="No team found")

    # Determine date-based subfolder
    now = datetime.now(UTC)
    date_folder = now.strftime("%Y/%m/%d")
    # Ensure the directory exists
    target_dir = os.path.join(UPLOAD_DIR, date_folder)
    os.makedirs(target_dir, exist_ok=True)

    # Generate unique filename
    file_ext = os.path.splitext(file.filename)[1] if file.filename else ""
    filename = f"{uuid.uuid4()}{file_ext}"
    # Relative path from UPLOAD_DIR (for storage)
    relative_path = os.path.join(date_folder, filename)
    # Absolute disk path
    storage_path = os.path.join(UPLOAD_DIR, relative_path)

    # Read file content
    content = await file.read()

    # Cap library storage size (see MEDIA_MAX_EDGE); keep original for video.
    width = None
    height = None
    mime = file.content_type
    if mime and mime.startswith("image/"):
        from app.services.media_storage import downscale_image_bytes

        content, width, height = downscale_image_bytes(content)

    # Write file to disk
    async with aiofiles.open(storage_path, "wb") as f:
        await f.write(content)

    asset = MediaAsset(
        team_id=team.id,
        user_id=current_user.id,
        filename=file.filename,
        mime_type=mime,
        size_bytes=len(content),
        width=width,
        height=height,
        storage_path=relative_path,  # Store relative path
        alt_text=alt_text,
        tags=tags.split(",") if tags else [],
        source="upload",
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)

    return asset


@router.get("/view")
async def view_media(path: str = Query(..., description="Relative storage path of the asset")):
    """Serve any stored media for display, converting non-web formats to PNG.

    Unauthenticated by design — mirrors the public ``/api/v1/uploads`` static
    mount so ``<img>`` tags can render assets without auth headers.  Formats
    browsers cannot render natively (TIFF, PSD, HEIC on Chromium, …) are
    transparently re-encoded to PNG with Pillow.
    """
    # Path traversal guard: resolved path must stay inside UPLOAD_DIR.
    abs_path = os.path.realpath(os.path.join(UPLOAD_DIR, path))
    if not abs_path.startswith(os.path.realpath(UPLOAD_DIR) + os.sep):
        raise HTTPException(status_code=400, detail="Invalid media path")

    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="Media file not found")

    ext_mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
        ".avif": "image/avif",
        ".bmp": "image/bmp",
        ".ico": "image/x-icon",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".mov": "video/quicktime",
    }
    mime = ext_mime.get(os.path.splitext(abs_path)[1].lower(), "application/octet-stream")

    if mime.startswith("video/") or mime in BROWSER_NATIVE_IMAGE_TYPES:
        return FileResponse(abs_path, media_type=mime)

    # Non-native image format → re-encode to PNG so every browser can show it.
    try:
        img = Image.open(abs_path)
        img.load()
    except Exception:
        raise HTTPException(
            status_code=415,
            detail="This format cannot be previewed in the browser. Download the file to view it.",
        )
    buf = io.BytesIO()
    if img.mode not in ("RGB", "RGBA", "L"):
        img = img.convert("RGBA")
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


@router.get("/assets", response_model=MediaListResponse)
async def list_media(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    type: str | None = Query(None, description="Filter: 'image' | 'video' | 'generated' (AI Generated)"),
    source: str | None = Query(None, description="Filter by exact source (e.g. 'upload', 'comfyui', 'ai-generated')"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()
    if not team:
        return MediaListResponse(assets=[], total=0, page=page, page_size=page_size)

    query = select(MediaAsset).where(MediaAsset.team_id == team.id)
    if source:
        query = query.where(MediaAsset.source == source)

    # Friendly type filters used by the Media Library UI.
    if type == "image":
        query = query.where(MediaAsset.mime_type.like("image/%"))
    elif type == "video":
        query = query.where(MediaAsset.mime_type.like("video/%"))
    elif type in ("generated", "ai-generated"):
        query = query.where(MediaAsset.source.in_(["ai-generated", "comfyui"]))

    from sqlalchemy import func
    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query) or 0

    query = query.order_by(MediaAsset.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    assets = result.scalars().all()

    return MediaListResponse(assets=assets, total=total, page=page, page_size=page_size)


@router.get("/assets/{asset_id}", response_model=MediaAssetResponse)
async def get_media(asset_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(MediaAsset).where(MediaAsset.id == asset_id)
    )
    asset = result.scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset


@router.delete("/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_media(asset_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(MediaAsset).where(MediaAsset.id == asset_id))
    asset = result.scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    # Delete file
    if os.path.exists(asset.storage_path):
        os.remove(asset.storage_path)

    await db.delete(asset)
    await db.commit()


class MediaGenerateOptions(BaseModel):
    width: int | None = None
    height: int | None = None
    model: str | None = None
    negative_prompt: str = ""
    steps: int = 4
    cfg_scale: float = 3.5


class MediaGenerateImageRequest(BaseModel):
    prompt: str
    options: MediaGenerateOptions | None = None
    workflow_json: str | dict | None = None  # legacy ComfyUI field — ignored


@router.post("/generate-image", response_model=MediaAssetResponse)
async def generate_image(
    body: MediaGenerateImageRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate an image via Cloudflare Workers AI (FLUX schnell) and store it."""
    import base64

    from app.services.cf_models import CF_TXT2IMG_FREE
    from app.services.inference import _call_workers_ai_image
    from app.services.media_storage import persist_generated_image

    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()
    if not team:
        raise HTTPException(status_code=400, detail="No team found")

    opts = body.options or MediaGenerateOptions()
    model = opts.model or CF_TXT2IMG_FREE
    if model and not model.startswith("@cf/"):
        # Legacy short names from old workflow templates
        model = f"@cf/stabilityai/{model}" if "stable-diffusion" in model else CF_TXT2IMG_FREE

    try:
        generated = await _call_workers_ai_image(
            prompt=body.prompt,
            model=model,
            negative_prompt=opts.negative_prompt or "",
            width=opts.width or 768,
            height=opts.height or 768,
            steps=opts.steps or 4,
            cfg_scale=opts.cfg_scale or 3.5,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Image generation failed: {exc}") from exc

    image_b64 = generated.get("image_base64") or ""
    if not image_b64:
        raise HTTPException(status_code=502, detail="Image generation returned empty payload")

    asset = await persist_generated_image(
        db,
        team_id=team.id,
        user_id=current_user.id,
        image_bytes=base64.b64decode(image_b64),
        prompt=body.prompt,
        source="ai-generated",
    )
    return asset


# Need to import User
