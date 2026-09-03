import io
import os
import pathlib
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, Response
from PIL import Image
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.core.config import settings
from app.core.path_utils import safe_resolve
from app.db.session import get_db
from app.models.content import MediaAsset, MediaCollection
from app.models.user import Team, TeamMember, User
from app.services import minio_storage, r2_presigned, r2_storage
from app.services.media_ai import get_similar_assets
from app.services.media_spellcheck import correct_tags, correct_text
from app.services.media_storage import downscale_image_bytes, persist_generated_image, save_uploaded_media
from app.worker.celery_app import celery_app

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
    collection_id: uuid.UUID | None
    filename: str | None
    mime_type: str | None
    size_bytes: int | None
    storage_backend: str
    storage_path: str
    public_url: str | None
    width: int | None
    height: int | None
    duration_seconds: int | None
    alt_text: str | None
    tags: list[str]
    ai_tags: list[str]
    ai_caption: str | None
    source: str
    generation_prompt: str | None
    is_favorite: bool
    is_archived: bool
    usage_count: int
    created_at: datetime
    updated_at: datetime

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

    # Validate and sanitize user-supplied filename and extension.
    raw_name = file.filename or "upload"
    raw_ext = pathlib.Path(raw_name).suffix.lower()
    allowed_exts = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".webm", ".mov", ".avif", ".heic", ".mp3", ".wav", ".aac", ".m4a", ".ogg", ".flac"}
    if raw_ext not in allowed_exts:
        raise HTTPException(status_code=400, detail="Unsupported file extension")

    # Read file content and downscale images for the library.
    content = await file.read()
    mime = file.content_type
    width = None
    height = None
    if mime and mime.startswith("image/"):
        content, width, height = downscale_image_bytes(content)

    asset = await save_uploaded_media(
        db,
        team_id=team.id,
        user_id=current_user.id,
        original_filename=file.filename or "upload",
        content=content,
        mime_type=mime,
        alt_text=alt_text,
        tags=[t.strip() for t in (tags or "").split(",") if t.strip()],
        width=width,
        height=height,
    )
    return asset


@router.get("/view")
async def view_media(path: str = Query(..., description="Relative storage path or object key of the asset")):
    """Serve any stored media for display, converting non-web formats to PNG.

    Unauthenticated by design — mirrors the public ``/api/v1/uploads`` static
    mount so ``<img>`` tags can render assets without auth headers.  Formats
    browsers cannot render natively (TIFF, PSD, HEIC on Chromium, …) are
    transparently re-encoded to PNG with Pillow.

    Tries local disk first, then MinIO, then R2 — so assets stored on any
    backend can be served through this single endpoint.
    """
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
        ".pdf": "application/pdf",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".aac": "audio/aac",
        ".m4a": "audio/mp4",
        ".ogg": "audio/ogg",
        ".flac": "audio/flac",
    }

    # Determine the file extension / mime type from the path (works for all backends).
    ext = pathlib.Path(path).suffix.lower()
    mime = ext_mime.get(ext, "application/octet-stream")

    # Formats that browsers can display/download directly without Pillow conversion.
    DIRECT_SERVE_TYPES = BROWSER_NATIVE_IMAGE_TYPES | {
        "video/mp4", "video/webm", "video/quicktime",
        "application/pdf",
        "audio/mpeg", "audio/wav", "audio/aac", "audio/mp4", "audio/ogg", "audio/flac",
    }

    # --- Try local disk first ---
    try:
        target = safe_resolve(UPLOAD_DIR, path)
        if target.is_file():
            if mime in DIRECT_SERVE_TYPES:
                return FileResponse(str(target), media_type=mime)
            try:
                buf = target.read_bytes()
                img = Image.open(io.BytesIO(buf))
                img.load()
            except Exception:
                raise HTTPException(
                    status_code=415,
                    detail="This format cannot be previewed in the browser. Download the file to view it.",
                )
            buf = io.BytesIO()
            out = img if img.mode in ("RGB", "RGBA", "L") else img.convert("RGBA")
            out.save(buf, format="PNG")
            return Response(content=buf.getvalue(), media_type="image/png")
    except ValueError:
        pass  # path not valid for local filesystem — try remote backends
    except HTTPException:
        raise

    # --- Try MinIO ---
    if minio_storage.minio_enabled():
        try:
            data = await minio_storage.get_object(path)
            if data:
                if mime in DIRECT_SERVE_TYPES:
                    return Response(content=data, media_type=mime)
                try:
                    img = Image.open(io.BytesIO(data))
                    img.load()
                except Exception:
                    raise HTTPException(
                        status_code=415,
                        detail="This format cannot be previewed in the browser.",
                    )
                buf = io.BytesIO()
                out = img if img.mode in ("RGB", "RGBA", "L") else img.convert("RGBA")
                out.save(buf, format="PNG")
                return Response(content=buf.getvalue(), media_type="image/png")
        except HTTPException as exc:
            if exc.status_code == 404:
                pass  # not in MinIO — try R2
            else:
                raise

    # --- Try R2 ---
    try:
        data = await r2_storage.get_object(path)
        if data:
            if mime in DIRECT_SERVE_TYPES:
                return Response(content=data, media_type=mime)
            try:
                img = Image.open(io.BytesIO(data))
                img.load()
            except Exception:
                raise HTTPException(
                    status_code=415,
                    detail="This format cannot be previewed in the browser.",
                )
            buf = io.BytesIO()
            out = img if img.mode in ("RGB", "RGBA", "L") else img.convert("RGBA")
            out.save(buf, format="PNG")
            return Response(content=buf.getvalue(), media_type="image/png")
    except HTTPException:
        pass

    raise HTTPException(status_code=404, detail="Media file not found on any storage backend")


@router.get("/assets", response_model=MediaListResponse)
async def list_media(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    type: str | None = Query(None, description="Filter: 'image' | 'video' | 'generated' (AI Generated)"),
    source: str | None = Query(None, description="Filter by exact source (e.g. 'upload', 'comfyui', 'ai-generated')"),
    sort: str | None = Query(None, description="Sort: 'newest' | 'oldest' | 'largest' | 'smallest' | 'name_asc' | 'name_desc'"),
    search: str | None = Query(None, description="Search filename, alt_text, and generation_prompt"),
    collection_id: uuid.UUID | None = Query(None, description="Filter to assets belonging to a specific collection"),
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

    if collection_id is not None:
        query = query.where(MediaAsset.collection_id == collection_id)

    if search:
        pattern = f"%{search}%"
        query = query.where(
            or_(
                MediaAsset.filename.ilike(pattern),
                MediaAsset.alt_text.ilike(pattern),
                MediaAsset.generation_prompt.ilike(pattern),
            )
        )

    from sqlalchemy import func

    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query) or 0

    sort_map = {
        "oldest": MediaAsset.created_at.asc(),
        "largest": MediaAsset.size_bytes.desc().nulls_last(),
        "smallest": MediaAsset.size_bytes.asc().nulls_last(),
        "name_asc": MediaAsset.filename.asc().nulls_last(),
        "name_desc": MediaAsset.filename.desc().nulls_last(),
    }
    order = sort_map.get(sort or "", MediaAsset.created_at.desc())
    query = query.order_by(order).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    assets = result.scalars().all()

    return MediaListResponse(assets=assets, total=total, page=page, page_size=page_size)


class MediaAssetUpdateRequest(BaseModel):
    filename: str | None = None
    alt_text: str | None = None
    tags: list[str] | None = None
    collection_id: uuid.UUID | None = None
    is_favorite: bool | None = None
    is_archived: bool | None = None


@router.get("/assets/{asset_id}", response_model=MediaAssetResponse)
async def get_media(asset_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(MediaAsset).where(MediaAsset.id == asset_id)
    )
    asset = result.scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset


@router.patch("/assets/{asset_id}", response_model=MediaAssetResponse)
async def update_media(
    asset_id: uuid.UUID,
    body: MediaAssetUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(MediaAsset).where(MediaAsset.id == asset_id)
    )
    asset = result.scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    if body.filename is not None:
        asset.filename = (await correct_text(body.filename)) or body.filename
    if body.alt_text is not None:
        asset.alt_text = await correct_text(body.alt_text)
    if body.tags is not None:
        asset.tags = await correct_tags(body.tags)
    if body.collection_id is not None:
        asset.collection_id = body.collection_id
    if body.is_favorite is not None:
        asset.is_favorite = body.is_favorite
    if body.is_archived is not None:
        asset.is_archived = body.is_archived

    await db.commit()
    await db.refresh(asset)
    return asset


@router.delete("/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_media(asset_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(MediaAsset).where(MediaAsset.id == asset_id))
    asset = result.scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    # Delete file from local disk, R2, or MinIO.
    try:
        if asset.storage_backend == "r2":
            await r2_storage.delete_object(asset.storage_path)
        elif asset.storage_backend == "minio":
            await minio_storage.delete_object(asset.storage_path)
        else:
            file_path = safe_resolve(UPLOAD_DIR, asset.storage_path)
            if file_path.is_file():
                file_path.unlink()
    except (ValueError, HTTPException):
        pass

    await db.delete(asset)
    await db.commit()


class BulkDeleteRequest(BaseModel):
    ids: list[uuid.UUID]


@router.post("/assets/bulk-delete", status_code=status.HTTP_200_OK)
async def bulk_delete_media(
    body: BulkDeleteRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()
    if not team:
        raise HTTPException(status_code=400, detail="No team found")

    result = await db.execute(
        select(MediaAsset).where(
            MediaAsset.id.in_(body.ids),
            MediaAsset.team_id == team.id,
        )
    )
    assets = result.scalars().all()
    deleted = 0
    for asset in assets:
        try:
            if asset.storage_backend == "r2":
                await r2_storage.delete_object(asset.storage_path)
            elif asset.storage_backend == "minio":
                await minio_storage.delete_object(asset.storage_path)
            else:
                file_path = safe_resolve(UPLOAD_DIR, asset.storage_path)
                if file_path.is_file():
                    file_path.unlink()
        except (ValueError, HTTPException):
            pass
        await db.delete(asset)
        deleted += 1
    await db.commit()
    return {"deleted": deleted}


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
    """Generate an image via Cloudflare Workers AI (FLUX schnell) with fallback chain."""
    import base64

    from app.services.cf_models import CF_TXT2IMG_FREE
    from app.services.inference import (
        HF_TXT2IMG_FALLBACK,
        TOGETHER_TXT2IMG_FALLBACK,
        _call_hf_txt2img,
        _call_pixazo_txt2img,
        _call_together_txt2img,
        _call_workers_ai_image,
    )
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()
    if not team:
        raise HTTPException(status_code=400, detail="No team found")

    # Spell-check the prompt and negative prompt before generation.
    prompt = await correct_text(body.prompt) or body.prompt
    opts = body.options or MediaGenerateOptions()
    opts.negative_prompt = await correct_text(opts.negative_prompt) or opts.negative_prompt
    model = opts.model or CF_TXT2IMG_FREE
    if model and not model.startswith("@cf/"):
        model = f"@cf/stabilityai/{model}" if "stable-diffusion" in model else CF_TXT2IMG_FREE

    width = opts.width or 1024
    height = opts.height or 1024
    steps = opts.steps or 4

    generated = None
    try:
        generated = await _call_workers_ai_image(
            prompt=prompt,
            model=model,
            negative_prompt=opts.negative_prompt or "",
            width=width,
            height=height,
            steps=steps,
            cfg_scale=opts.cfg_scale or 3.5,
        )
    except HTTPException as exc:
        print(f"[media/generate] CF failed ({exc.status_code}), trying fallbacks", flush=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Image generation failed: {exc}") from exc

    if generated is None:
        pixazo_key = (settings.PIXAZO_API_KEY or "").strip()
        together_key = (settings.TOGETHER_API_KEY or "").strip()
        hf_key = (settings.HUGGINGFACE_API_KEY or "").strip()

        if pixazo_key:
            print("[media/generate] Trying Pixazo FLUX Schnell", flush=True)
            try:
                generated = await _call_pixazo_txt2img(
                    prompt=prompt, api_key=pixazo_key,
                    width=width, height=height,
                )
            except HTTPException:
                print("[media/generate] Pixazo failed", flush=True)

        if generated is None and together_key:
            print(f"[media/generate] Trying Together {TOGETHER_TXT2IMG_FALLBACK}", flush=True)
            try:
                generated = await _call_together_txt2img(
                    prompt=prompt, model=TOGETHER_TXT2IMG_FALLBACK,
                    api_key=together_key, width=width, height=height, steps=steps,
                )
            except HTTPException:
                print("[media/generate] Together failed", flush=True)

        if generated is None and hf_key:
            print(f"[media/generate] Trying HF {HF_TXT2IMG_FALLBACK}", flush=True)
            try:
                generated = await _call_hf_txt2img(
                    prompt=prompt, model=HF_TXT2IMG_FALLBACK,
                    api_key=hf_key, width=width, height=height, steps=steps,
                )
            except HTTPException:
                print("[media/generate] HF also failed", flush=True)

        if generated is None:
            raise HTTPException(
                status_code=502,
                detail="All image generation providers exhausted (CF quota + fallbacks failed)",
            )

    image_b64 = generated.get("image_base64") or ""
    if not image_b64:
        raise HTTPException(status_code=502, detail="Image generation returned empty payload")

    asset = await persist_generated_image(
        db,
        team_id=team.id,
        user_id=current_user.id,
        image_bytes=base64.b64decode(image_b64),
        prompt=prompt,
        source="ai-generated",
    )
    return asset


# ---------------------------------------------------------------------------
# Presigned R2 upload flow
# ---------------------------------------------------------------------------

class PresignedUploadRequest(BaseModel):
    filename: str
    mime_type: str
    size_bytes: int
    alt_text: str | None = None
    tags: list[str] | None = None


class PresignedUploadResponse(BaseModel):
    key: str
    upload_url: str
    public_url: str | None


class CompleteUploadRequest(BaseModel):
    key: str
    filename: str
    mime_type: str
    size_bytes: int
    width: int | None = None
    height: int | None = None
    alt_text: str | None = None
    tags: list[str] | None = None


@router.post("/upload/prepare", response_model=PresignedUploadResponse)
async def prepare_upload(
    body: PresignedUploadRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()
    if not team:
        raise HTTPException(status_code=400, detail="No team found")

    url = r2_presigned.presigned_upload_url(
        team_id=str(team.id),
        filename=body.filename,
        mime_type=body.mime_type,
        size_bytes=body.size_bytes,
    )
    if not url:
        # Fall back to MinIO presigned URL if R2 S3 credentials are not configured
        url = minio_storage.presigned_upload_url(
            team_id=str(team.id),
            filename=body.filename,
            mime_type=body.mime_type,
            size_bytes=body.size_bytes,
        )
    if not url:
        raise HTTPException(
            status_code=503,
            detail="Neither R2 nor MinIO S3 credentials are configured. Use the server-side /upload endpoint instead.",
        )
    return url


@router.post("/upload/complete", response_model=MediaAssetResponse, status_code=status.HTTP_201_CREATED)
async def complete_upload(
    body: CompleteUploadRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()
    if not team:
        raise HTTPException(status_code=400, detail="No team found")

    corrected_alt = await correct_text(body.alt_text)
    corrected_tags = await correct_tags(body.tags or [])
    public_url = r2_presigned._public_url(body.key)

    asset = MediaAsset(
        team_id=team.id,
        user_id=current_user.id,
        filename=body.filename,
        mime_type=body.mime_type,
        size_bytes=body.size_bytes,
        storage_backend="r2",
        storage_path=body.key,
        public_url=public_url,
        width=body.width,
        height=body.height,
        alt_text=corrected_alt,
        tags=corrected_tags,
        source="upload",
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)
    try:
        celery_app.send_task("app.worker.tasks.media.auto_tag_asset_task", args=[str(asset.id)])
    except Exception:
        pass
    return asset


# ---------------------------------------------------------------------------
# Collections
# ---------------------------------------------------------------------------

class MediaCollectionCreateRequest(BaseModel):
    name: str
    description: str | None = None
    cover_asset_id: uuid.UUID | None = None


class MediaCollectionUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    cover_asset_id: uuid.UUID | None = None


class MediaCollectionResponse(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID
    user_id: uuid.UUID | None
    name: str
    description: str | None
    cover_asset_id: uuid.UUID | None
    asset_count: int = 0
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class CollectionListResponse(BaseModel):
    collections: list[MediaCollectionResponse]
    total: int


@router.post("/collections", response_model=MediaCollectionResponse, status_code=status.HTTP_201_CREATED)
async def create_collection(
    body: MediaCollectionCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()
    if not team:
        raise HTTPException(status_code=400, detail="No team found")

    corrected_name = (await correct_text(body.name)) or body.name
    collection = MediaCollection(
        team_id=team.id,
        user_id=current_user.id,
        name=corrected_name,
        description=body.description,
        cover_asset_id=body.cover_asset_id,
    )
    db.add(collection)
    await db.commit()
    await db.refresh(collection)

    response = MediaCollectionResponse.model_validate(collection)
    response.asset_count = 0
    return response


@router.get("/collections", response_model=CollectionListResponse)
async def list_collections(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()
    if not team:
        raise HTTPException(status_code=400, detail="No team found")

    result = await db.execute(
        select(MediaCollection).where(MediaCollection.team_id == team.id)
    )
    collections = result.scalars().all()

    items = []
    for col in collections:
        count = await db.execute(
            select(MediaAsset).where(MediaAsset.collection_id == col.id)
        )
        item = MediaCollectionResponse.model_validate(col)
        item.asset_count = len(count.scalars().all())
        items.append(item)

    return CollectionListResponse(collections=items, total=len(items))


@router.get("/collections/{collection_id}", response_model=MediaCollectionResponse)
async def get_collection(
    collection_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(MediaCollection).where(MediaCollection.id == collection_id)
    )
    collection = result.scalar_one_or_none()
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")

    count = await db.execute(
        select(MediaAsset).where(MediaAsset.collection_id == collection_id)
    )
    response = MediaCollectionResponse.model_validate(collection)
    response.asset_count = len(count.scalars().all())
    return response


@router.patch("/collections/{collection_id}", response_model=MediaCollectionResponse)
async def update_collection(
    collection_id: uuid.UUID,
    body: MediaCollectionUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(MediaCollection).where(MediaCollection.id == collection_id)
    )
    collection = result.scalar_one_or_none()
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")

    if body.name is not None:
        collection.name = (await correct_text(body.name)) or body.name
    if body.description is not None:
        collection.description = body.description
    if body.cover_asset_id is not None:
        collection.cover_asset_id = body.cover_asset_id

    await db.commit()
    await db.refresh(collection)

    count = await db.execute(
        select(MediaAsset).where(MediaAsset.collection_id == collection_id)
    )
    response = MediaCollectionResponse.model_validate(collection)
    response.asset_count = len(count.scalars().all())
    return response


@router.delete("/collections/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_collection(
    collection_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(MediaCollection).where(MediaCollection.id == collection_id)
    )
    collection = result.scalar_one_or_none()
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")

    await db.delete(collection)
    await db.commit()


class CollectionAssetRequest(BaseModel):
    asset_id: uuid.UUID


@router.post("/collections/{collection_id}/assets", response_model=MediaAssetResponse)
async def add_asset_to_collection(
    collection_id: uuid.UUID,
    body: CollectionAssetRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(MediaCollection, MediaAsset)
        .join(MediaAsset, MediaAsset.id == body.asset_id)
        .where(
            MediaCollection.id == collection_id,
            MediaCollection.team_id == MediaAsset.team_id,
        )
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="Collection or asset not found or not in the same team")

    collection, asset = row
    asset.collection_id = collection.id
    await db.commit()
    await db.refresh(asset)
    return asset


@router.delete("/collections/{collection_id}/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_asset_from_collection(
    collection_id: uuid.UUID,
    asset_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(MediaAsset).where(
            MediaAsset.id == asset_id,
            MediaAsset.collection_id == collection_id,
        )
    )
    asset = result.scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found in collection")

    asset.collection_id = None
    await db.commit()


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@router.get("/search", response_model=MediaListResponse)
async def search_media(
    q: str | None = Query(None),
    mime_type: str | None = Query(None),
    source: str | None = Query(None),
    collection_id: uuid.UUID | None = Query(None),
    tags: list[str] | None = Query(None),
    is_favorite: bool | None = Query(None),
    is_archived: bool | None = Query(None),
    sort: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()
    if not team:
        raise HTTPException(status_code=400, detail="No team found")

    query = select(MediaAsset).where(MediaAsset.team_id == team.id)

    if q:
        query = query.where(
            or_(
                MediaAsset.filename.ilike(f"%{q}%"),
                MediaAsset.alt_text.ilike(f"%{q}%"),
                MediaAsset.ai_caption.ilike(f"%{q}%"),
                MediaAsset.tags.any(q),
                MediaAsset.ai_tags.any(q),
            )
        )

    if mime_type:
        query = query.where(MediaAsset.mime_type.ilike(f"%{mime_type}%"))
    if source:
        query = query.where(MediaAsset.source == source)
    if collection_id:
        query = query.where(MediaAsset.collection_id == collection_id)
    if is_favorite is not None:
        query = query.where(MediaAsset.is_favorite == is_favorite)
    if is_archived is not None:
        query = query.where(MediaAsset.is_archived == is_archived)
    if tags:
        query = query.where(MediaAsset.tags.overlap(tags))

    count_result = await db.execute(query.with_only_columns(MediaAsset.id))
    total = len(count_result.scalars().all())

    sort_map = {
        "newest": MediaAsset.created_at.desc(),
        "oldest": MediaAsset.created_at.asc(),
        "name": MediaAsset.filename.asc(),
        "size": MediaAsset.size_bytes.desc(),
    }
    order = sort_map.get(sort or "", MediaAsset.created_at.desc())
    query = query.order_by(order).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    assets = result.scalars().all()

    return MediaListResponse(assets=assets, total=total, page=page, page_size=page_size)


# ---------------------------------------------------------------------------
# AI auto-tagging and similarity
# ---------------------------------------------------------------------------

class SimilarAssetResponse(BaseModel):
    asset_id: uuid.UUID | None
    ai_caption: str | None


@router.post("/assets/{asset_id}/tag", response_model=MediaAssetResponse)
async def retag_asset(
    asset_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()
    if not team:
        raise HTTPException(status_code=400, detail="No team found")

    result = await db.execute(
        select(MediaAsset).where(
            MediaAsset.id == asset_id,
            MediaAsset.team_id == team.id,
        )
    )
    asset = result.scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    try:
        celery_app.send_task("app.worker.tasks.media.auto_tag_asset_task", args=[str(asset.id)])
    except Exception:
        pass
    return asset


@router.get("/assets/{asset_id}/similar", response_model=list[SimilarAssetResponse])
async def similar_assets(
    asset_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()
    if not team:
        raise HTTPException(status_code=400, detail="No team found")

    result = await db.execute(
        select(MediaAsset).where(
            MediaAsset.id == asset_id,
            MediaAsset.team_id == team.id,
        )
    )
    asset = result.scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    similar = await get_similar_assets(team.id, asset.id)
    if not similar:
        return []

    ids = [uuid.UUID(s["embedding_id"]) for s in similar if s.get("embedding_id")]
    result = await db.execute(
        select(MediaAsset.id, MediaAsset.ai_caption).where(
            MediaAsset.id.in_(ids),
            MediaAsset.team_id == team.id,
        )
    )
    rows = result.all()
    return [SimilarAssetResponse(asset_id=row[0], ai_caption=row[1]) for row in rows]


# Need to import User
