"""AI auto-tagging for media assets using Cloudflare Workers AI vision models.

Cloudflare-first, free-tier:
- `@cf/moondream/moondream3.1-9B-A2B` for image captioning and keyword query.
- Ollama `llava` is used as a last-resort fallback if Cloudflare is unavailable.

The service is best-effort: failures are logged and do not block uploads.
"""
from __future__ import annotations

import base64
import io
import logging
import os
import uuid

import httpx
from PIL import Image
from sqlalchemy import select

from app.core.config import get_settings
from app.core.path_utils import safe_resolve
from app.db.session import async_session_maker
from app.models.content import MediaAsset
from app.services import chroma_client, r2_storage
from app.services.media_spellcheck import correct_tags, correct_text

logger = logging.getLogger(__name__)
settings = get_settings()

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/app/uploads")

CF_VISION_MODEL = "@cf/moondream/moondream3.1-9B-A2B"
OLLAMA_VISION_MODEL = "llava"


def _data_uri(image_bytes: bytes, mime_type: str = "image/png") -> str:
    """Convert image bytes to a base64 data URI for Workers AI."""
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{mime_type};base64,{b64}"


async def _load_image_bytes(asset: MediaAsset) -> bytes | None:
    """Load image bytes from R2 or local disk."""
    try:
        if asset.storage_backend == "r2":
            return await r2_storage.get_object(asset.storage_path)
        return safe_resolve(UPLOAD_DIR, asset.storage_path).read_bytes()
    except Exception as exc:
        logger.warning("Failed to load image bytes for asset %s: %s", asset.id, exc)
        return None


async def _call_cloudflare_vision(
    image_b64: str,
    task: str,
    prompt: str,
    max_tokens: int = 512,
) -> dict:
    """Call a Cloudflare Workers AI vision model."""
    account_id = (settings.CLOUDFLARE_ACCOUNT_ID or "").strip()
    token = (settings.CLOUDFLARE_API_TOKEN or "").strip()
    if not account_id or not token:
        raise RuntimeError("Cloudflare credentials not configured")

    from urllib.parse import quote

    model = quote(CF_VISION_MODEL, safe="@/")
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"

    payload: dict = {
        "task": task,
        "image": image_b64,
        "prompt": prompt,
        "max_tokens": max_tokens,
    }
    if task == "caption":
        payload["caption_length"] = "normal"

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
        )

    if resp.status_code != 200:
        raise RuntimeError(f"Cloudflare vision error {resp.status_code}: {resp.text[:500]}")

    return resp.json()


def _extract_caption(result: dict) -> str | None:
    """Extract caption text from a Moondream/LLaMA-vision response."""
    # Workers AI can wrap the response under `result` or return it directly.
    data = result.get("result") or result
    return data.get("description") or data.get("caption") or data.get("response") or data.get("text")


def _extract_query_text(result: dict) -> str | None:
    """Extract query answer text."""
    data = result.get("result") or result
    return data.get("description") or data.get("response") or data.get("text") or data.get("answer")


async def _call_ollama_vision(image_b64: str, prompt: str) -> dict:
    """Ollama last-resort fallback for vision tasks."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{settings.OLLAMA_URL}/api/generate",
            json={
                "model": OLLAMA_VISION_MODEL,
                "prompt": prompt,
                "images": [image_b64.split(",", 1)[-1]],
                "stream": False,
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(f"Ollama vision error {resp.status_code}: {resp.text[:500]}")
    return resp.json()


async def _caption_image(image_b64: str) -> str | None:
    """Generate a one-sentence caption."""
    try:
        result = await _call_cloudflare_vision(image_b64, "caption", "Generate a concise caption for this image")
        return _extract_caption(result)
    except Exception as exc:
        logger.warning("Cloudflare caption failed: %s", exc)

    try:
        result = await _call_ollama_vision(image_b64, "Describe this image in one sentence")
        return result.get("response")
    except Exception as exc:
        logger.warning("Ollama vision caption failed: %s", exc)
    return None


async def _tag_image(image_b64: str) -> list[str]:
    """Generate a comma-separated list of keywords/tags."""
    prompt = (
        "List 5 to 10 relevant keywords for this image as a comma-separated list. "
        "Use only single words or short phrases."
    )
    try:
        result = await _call_cloudflare_vision(image_b64, "query", prompt)
        text = _extract_query_text(result) or ""
    except Exception as exc:
        logger.warning("Cloudflare tag query failed: %s", exc)
        text = ""

    if not text:
        try:
            result = await _call_ollama_vision(image_b64, prompt)
            text = result.get("response") or ""
        except Exception as exc:
            logger.warning("Ollama tag query failed: %s", exc)

    if not text:
        return []

    tags = [t.strip(" -\"'").lower() for t in text.split(",") if t.strip()]
    # Remove obvious instruction remnants
    tags = [t for t in tags if t and not t.startswith("list") and not t.startswith("image")]
    return tags


def _resize_for_vision(image_bytes: bytes, max_edge: int = 768) -> tuple[bytes, str]:
    """Downscale large images before sending them to a vision model."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        w, h = img.size
        if max(w, h) > max_edge:
            scale = max_edge / float(max(w, h))
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        fmt = "PNG" if img.mode in ("RGBA", "P") else "JPEG"
        if fmt == "JPEG" and img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.save(buf, format=fmt, optimize=True)
        mime = "image/png" if fmt == "PNG" else "image/jpeg"
        return buf.getvalue(), mime
    except Exception:
        return image_bytes, "image/png"


async def auto_tag_asset(asset_id: uuid.UUID | str) -> None:
    """Generate AI caption/tags for an asset and index them in Chroma."""
    if isinstance(asset_id, str):
        asset_id = uuid.UUID(asset_id)
    async with async_session_maker() as db:
        result = await db.execute(select(MediaAsset).where(MediaAsset.id == asset_id))
        asset = result.scalar_one_or_none()
        if not asset:
            logger.warning("auto_tag_asset: asset %s not found", asset_id)
            return

        if asset.mime_type and not asset.mime_type.startswith("image/"):
            logger.info("auto_tag_asset: skipping non-image asset %s", asset_id)
            return

        image_bytes = await _load_image_bytes(asset)
        if not image_bytes:
            return

        resized, mime = _resize_for_vision(image_bytes)
        image_b64 = _data_uri(resized, mime)

        caption = await _caption_image(image_b64)
        tags = await _tag_image(image_b64)

        if caption:
            asset.ai_caption = await correct_text(caption)
        if tags:
            asset.ai_tags = await correct_tags(tags)

        embedding_id = str(asset_id)
        asset.embedding_id = embedding_id

        await db.commit()
        await db.refresh(asset)

        # Index the caption in Chroma for semantic search / similar images
        text = f"{asset.ai_caption or ''} {' '.join(asset.ai_tags or [])}".strip()
        if text:
            await chroma_client.add_content(str(asset.team_id), embedding_id, text)


async def get_similar_assets(team_id: uuid.UUID | str, asset_id: uuid.UUID | str, n_results: int = 5) -> list[dict]:
    """Return IDs and captions of similar assets from Chroma."""
    async with async_session_maker() as db:
        result = await db.execute(
            select(MediaAsset).where(MediaAsset.id == asset_id, MediaAsset.team_id == team_id)
        )
        asset = result.scalar_one_or_none()
        if not asset:
            return []

        text = f"{asset.ai_caption or ''} {' '.join(asset.ai_tags or [])}".strip()
        if not text:
            return []

        docs = await chroma_client.query_similar(str(team_id), text, n_results=n_results + 1)
        # Filter out the asset itself
        return [
            {"embedding_id": doc_id}
            for doc_id in docs
            if doc_id and doc_id != str(asset_id)
        ][:n_results]
