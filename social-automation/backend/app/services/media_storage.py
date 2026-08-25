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

_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


async def persist_generated_image(
    db: AsyncSession,
    *,
    team_id,
    user_id,
    image_bytes: bytes,
    prompt: str,
    source: str = "ai-generated",
    extension: str = ".png",
) -> MediaAsset:
    """Write generated image bytes under UPLOAD_DIR/YYYY/MM/DD/ and create a
    ``media_assets`` row so the asset shows up in the Media Library page."""
    now = datetime.now(UTC)
    date_folder = now.strftime("%Y/%m/%d")
    target_dir = os.path.join(UPLOAD_DIR, date_folder)
    os.makedirs(target_dir, exist_ok=True)

    filename = f"{source}_{uuid.uuid4().hex[:8]}{extension}"
    relative_path = os.path.join(date_folder, filename)
    abs_path = os.path.join(UPLOAD_DIR, relative_path)

    async with aiofiles.open(abs_path, "wb") as f:
        await f.write(image_bytes)

    width = None
    height = None
    try:
        img = Image.open(io.BytesIO(image_bytes))
        width, height = img.size
    except Exception:
        # If we cannot read the image dimensions, leave them as None
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
