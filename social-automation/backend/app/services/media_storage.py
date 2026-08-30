"""Persist generated images and uploaded files to local disk or Cloudflare R2.

R2 is the preferred, free storage target when ``R2_BUCKET_NAME`` and
``R2_PUBLIC_URL`` are configured.  Local disk remains a fallback for
self-hosted/offline deployments.  Every media text field is spell-checked on
save.
"""
from __future__ import annotations

import io
import os
import pathlib
import uuid
from datetime import UTC, datetime

import aiofiles
from fastapi import HTTPException
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.path_utils import safe_path_component, safe_resolve
from app.models.content import MediaAsset, StorageBackend
from app.services import r2_storage
from app.services.media_spellcheck import correct_tags, correct_text

settings = get_settings()

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/app/uploads")
# Cap longest edge for library storage (saves disk; zoom in viewer for detail).
# Set MEDIA_MAX_EDGE=0 to disable. Carousel slides pass max_edge=None to keep 1080.
MEDIA_MAX_EDGE = int(os.environ.get("MEDIA_MAX_EDGE", "768"))

_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def _r2_enabled() -> bool:
    return all([
        (settings.R2_BUCKET_NAME or "").strip(),
        (settings.CLOUDFLARE_ACCOUNT_ID or "").strip(),
        (settings.CLOUDFLARE_API_TOKEN or "").strip(),
    ])


def _public_local_url(storage_path: str) -> str | None:
    base = (settings.MEDIA_PUBLIC_BASE_URL or "").rstrip("/")
    if not base:
        return None
    return f"{base}/api/v1/media/view?path={storage_path}"


def downscale_image_bytes(
    image_bytes: bytes,
    *,
    max_edge: int | None = None,
) -> tuple[bytes, int | None, int | None]:
    """Downscale so the longest edge is ≤ max_edge. Returns (bytes, width, height).

    Animated GIFs and unreadable images are returned unchanged.
    ``max_edge`` None/≤0 skips resizing (dimensions still read when possible).
    """
    if max_edge is None:
        max_edge = MEDIA_MAX_EDGE

    try:
        img = Image.open(io.BytesIO(image_bytes))
    except Exception:
        return image_bytes, None, None

    # Don't flatten animated GIFs
    if getattr(img, "is_animated", False) and getattr(img, "n_frames", 1) > 1:
        return image_bytes, img.size[0], img.size[1]

    w, h = img.size
    if max_edge <= 0 or max(w, h) <= max_edge:
        return image_bytes, w, h

    scale = max_edge / float(max(w, h))
    new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
    resample = getattr(Image, "Resampling", Image).LANCZOS
    out = img.convert("RGBA") if img.mode in ("P", "LA") else img
    if out.mode == "P":
        out = out.convert("RGBA")
    out = out.resize(new_size, resample)

    buf = io.BytesIO()
    fmt = (img.format or "PNG").upper()
    if fmt in ("JPEG", "JPG"):
        if out.mode in ("RGBA", "P"):
            out = out.convert("RGB")
        out.save(buf, format="JPEG", quality=85, optimize=True)
    elif fmt == "WEBP":
        out.save(buf, format="WEBP", quality=85, method=4)
    else:
        if out.mode == "CMYK":
            out = out.convert("RGB")
        out.save(buf, format="PNG", optimize=True)

    return buf.getvalue(), new_size[0], new_size[1]


async def _store_bytes(
    data: bytes,
    filename: str,
    mime_type: str,
    date_folder: str,
) -> tuple[StorageBackend, str, str | None]:
    """Store bytes on R2 if enabled, otherwise local disk. Returns backend, path/key, public_url."""
    ext = pathlib.Path(filename).suffix.lower() or ".bin"
    safe_name = f"{uuid.uuid4().hex[:16]}{ext}"
    key = f"{date_folder}/{safe_name}"

    if _r2_enabled():
        try:
            r2 = await r2_storage.upload_object(key, data, content_type=mime_type)
            return StorageBackend.r2, key, r2.get("public_url")
        except HTTPException:
            pass

    abs_path = safe_resolve(UPLOAD_DIR, date_folder, safe_name)
    upload_root = pathlib.Path(UPLOAD_DIR).resolve()
    relative_path = abs_path.relative_to(upload_root).as_posix()
    abs_path.parent.mkdir(parents=True, exist_ok=True)

    async with aiofiles.open(str(abs_path), "wb") as f:
        await f.write(data)

    return StorageBackend.local, relative_path, _public_local_url(relative_path)


async def save_uploaded_media(
    db: AsyncSession,
    *,
    team_id,
    user_id,
    original_filename: str,
    content: bytes,
    mime_type: str,
    alt_text: str | None,
    tags: list[str],
    width: int | None,
    height: int | None,
) -> MediaAsset:
    """Save an uploaded file to local or R2 and create a MediaAsset row."""
    now = datetime.now(UTC)
    date_folder = now.strftime("%Y/%m/%d")

    backend, storage_path, public_url = await _store_bytes(
        content, original_filename, mime_type, date_folder
    )

    corrected_alt = await correct_text(alt_text)
    corrected_tags = await correct_tags(tags)

    asset = MediaAsset(
        team_id=team_id,
        user_id=user_id,
        filename=original_filename,
        mime_type=mime_type,
        size_bytes=len(content),
        width=width,
        height=height,
        storage_backend=backend,
        storage_path=storage_path,
        public_url=public_url,
        alt_text=corrected_alt,
        tags=corrected_tags,
        source="upload",
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)
    return asset


async def persist_generated_image(
    db: AsyncSession,
    *,
    team_id,
    user_id,
    image_bytes: bytes,
    prompt: str,
    source: str = "ai-generated",
    extension: str = ".png",
    max_edge: int | None = None,
    folder: str | None = None,
) -> MediaAsset:
    """Write generated image bytes under UPLOAD_DIR/YYYY/MM/DD/ or R2 and create a
    ``media_assets`` row so the asset shows up in the Media Library page.

    Pass ``max_edge=None`` with env MEDIA_MAX_EDGE for default cap, or an int to
    override. Pass ``max_edge=0`` to store full resolution (e.g. LinkedIn carousels).
    """
    if max_edge is None:
        # Carousel / branded slides need full LinkedIn size
        if source in ("n8n-cf-pipe", "carousel", "comfyui-carousel"):
            max_edge = 0
        else:
            max_edge = MEDIA_MAX_EDGE

    image_bytes, width, height = downscale_image_bytes(image_bytes, max_edge=max_edge)

    now = datetime.now(UTC)
    date_part = now.strftime("%Y/%m/%d")

    # Sanitize user-supplied folder to a safe path component.
    safe_folder = safe_path_component(folder) if folder else ""
    date_folder = f"{date_part}/{safe_folder}" if safe_folder else date_part

    # Sanitize source and extension for the filename.
    safe_source = safe_path_component(source, max_length=48)
    safe_ext = safe_path_component(extension.lower(), max_length=16).lstrip(".")
    safe_ext = f".{safe_ext}" if safe_ext else ".bin"
    filename = f"{safe_source}_{uuid.uuid4().hex[:8]}{safe_ext}"

    backend, storage_path, public_url = await _store_bytes(
        image_bytes, filename, _MIME_BY_EXT.get(safe_ext, "application/octet-stream"), date_folder
    )

    if width is None or height is None:
        try:
            img = Image.open(io.BytesIO(image_bytes))
            width, height = img.size
        except Exception:
            pass

    corrected_prompt = await correct_text(prompt)
    alt = corrected_prompt or ""
    tags = await correct_tags(["ai-generated"] if source == "ai-generated" else [])

    asset = MediaAsset(
        team_id=team_id,
        user_id=user_id,
        filename=filename,
        mime_type=_MIME_BY_EXT.get(extension.lower(), "application/octet-stream"),
        size_bytes=len(image_bytes),
        width=width,
        height=height,
        storage_backend=backend,
        storage_path=storage_path,
        public_url=public_url,
        alt_text=alt,
        tags=tags,
        source=source,
        generation_prompt=corrected_prompt,
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)
    return asset
