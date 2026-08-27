"""Persist generated images to disk + database so they appear in the Media Library."""
import io
import os
import uuid
from datetime import UTC, datetime

import aiofiles
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content import MediaAsset

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
) -> MediaAsset:
    """Write generated image bytes under UPLOAD_DIR/YYYY/MM/DD/ and create a
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
    date_folder = now.strftime("%Y/%m/%d")
    target_dir = os.path.join(UPLOAD_DIR, date_folder)
    os.makedirs(target_dir, exist_ok=True)

    filename = f"{source}_{uuid.uuid4().hex[:8]}{extension}"
    relative_path = os.path.join(date_folder, filename)
    abs_path = os.path.join(UPLOAD_DIR, relative_path)

    async with aiofiles.open(abs_path, "wb") as f:
        await f.write(image_bytes)

    if width is None or height is None:
        try:
            img = Image.open(io.BytesIO(image_bytes))
            width, height = img.size
        except Exception:
            pass

    asset = MediaAsset(
        team_id=team_id,
        user_id=user_id,
        filename=filename,
        mime_type=_MIME_BY_EXT.get(extension.lower(), "application/octet-stream"),
        size_bytes=len(image_bytes),
        width=width,
        height=height,
        storage_path=relative_path,
        alt_text=prompt,
        tags=["ai-generated"] if source == "ai-generated" else [],
        source=source,
        generation_prompt=prompt,
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)
    return asset
