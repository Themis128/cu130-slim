"""AI auto-tagging for media assets using vision models.

DMR-first (local, free):
- `ai/qwen3-vl` (primary, local Docker Model Runner) — multimodal vision model,
  runs on local GPU via llama.cpp, no API key needed.

Cloudflare failover:
- `@cf/meta/llama-4-scout-17b-16e-instruct` (primary CF, 0.6 neurons) — multimodal,
  function calling, 40x cheaper than moondream.
- `@cf/moondream/moondream3.1-9B-A2B` (fallback CF, 24 neurons) — dedicated vision
  model with caption/query/point/detect tasks.

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
from app.services import chroma_client, minio_storage, r2_storage
from app.services.media_spellcheck import correct_tags, correct_text

logger = logging.getLogger(__name__)
settings = get_settings()

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/app/uploads")

# Primary vision model: llama-4-scout (0.6 neurons, multimodal, function calling)
CF_VISION_MODEL = "@cf/meta/llama-4-scout-17b-16e-instruct"
# Fallback vision model: moondream (24 neurons, dedicated vision tasks)
CF_VISION_FALLBACK_MODEL = "@cf/moondream/moondream3.1-9B-A2B"


def _data_uri(image_bytes: bytes, mime_type: str = "image/png") -> str:
    """Convert image bytes to a base64 data URI for Workers AI."""
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{mime_type};base64,{b64}"


def _raw_b64(image_bytes: bytes) -> str:
    """Convert image bytes to raw base64 (no data URI prefix) for llama-4."""
    return base64.b64encode(image_bytes).decode("utf-8")


async def _load_image_bytes(asset: MediaAsset) -> bytes | None:
    """Load image bytes from R2, MinIO, or local disk."""
    try:
        if asset.storage_backend == "r2":
            return await r2_storage.get_object(asset.storage_path)
        if asset.storage_backend == "minio":
            return await minio_storage.get_object(asset.storage_path)
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
    """Call a Cloudflare Workers AI vision model.

    Tries llama-4-scout first (chat-format multimodal, 0.6 neurons), then falls
    back to moondream (task-based vision, 24 neurons) if the primary model fails.
    """
    account_id = (settings.CLOUDFLARE_ACCOUNT_ID or "").strip()
    token = (settings.CLOUDFLARE_AI_API_TOKEN or "").strip() or (settings.CLOUDFLARE_API_TOKEN or "").strip()
    if not account_id or not token:
        raise RuntimeError("Cloudflare credentials not configured")

    from urllib.parse import quote

    # llama-4-scout uses OpenAI-compatible content arrays with image_url.
    # moondream uses the task/image format. Keep the data URI for llama-4.
    data_uri = image_b64 if image_b64.startswith("data:") else f"data:image/jpeg;base64,{image_b64}"

    # --- Primary: llama-4-scout (chat format, 0.6 neurons) ---
    try:
        model = quote(CF_VISION_MODEL, safe="@/")
        url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"
        if task == "caption":
            user_msg = "Generate a concise one-sentence caption for this image."
        else:
            user_msg = prompt
        payload = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_msg},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                },
            ],
            "max_tokens": max_tokens,
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if resp.status_code == 200:
            return resp.json()
        logger.warning("llama-4-scout vision returned %s, falling back to moondream", resp.status_code)
    except Exception as exc:
        logger.warning("llama-4-scout vision failed: %s, falling back to moondream", exc)

    # --- Fallback: moondream (task format, 24 neurons) ---
    model = quote(CF_VISION_FALLBACK_MODEL, safe="@/")
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"
    moondream_payload: dict = {
        "task": task,
        "image": image_b64,
        "stream": False,
        "max_tokens": max_tokens,
    }
    if task == "caption":
        moondream_payload["caption_length"] = "normal"
    elif task == "query":
        moondream_payload["question"] = prompt
    else:
        moondream_payload["prompt"] = prompt

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=moondream_payload,
        )

    if resp.status_code != 200:
        raise RuntimeError(f"Cloudflare vision error {resp.status_code}: {resp.text[:500]}")

    return resp.json()


def _extract_caption(result: dict) -> str | None:
    """Extract caption text from a vision model response.

    Handles both llama-4-scout (``result.response``) and moondream
    (``result.result.caption``) response formats, plus legacy ``text``/``description``.
    """
    data = result.get("result") or result
    inner = data.get("result") or data
    # llama-4-scout: result.response is the text
    text = data.get("response") or inner.get("response")
    if text:
        return text
    # moondream: result.result.caption
    return (
        inner.get("caption")
        or data.get("caption")
        or inner.get("description")
        or data.get("description")
        or inner.get("text")
        or data.get("text")
    )


def _extract_query_text(result: dict) -> str | None:
    """Extract query answer text from a vision model response.

    Handles both llama-4-scout (``result.response``) and moondream
    (``result.result.answer``) response formats, plus legacy ``text``/``description``.
    """
    data = result.get("result") or result
    inner = data.get("result") or data
    # llama-4-scout: result.response is the text
    text = data.get("response") or inner.get("response")
    if text:
        return text
    # moondream: result.result.answer
    return (
        inner.get("answer")
        or data.get("answer")
        or inner.get("description")
        or data.get("description")
        or inner.get("text")
        or data.get("text")
    )


async def _call_dmr_vision(image_b64: str, prompt: str, max_tokens: int = 512) -> str | None:
    """Call Docker Model Runner (qwen3-vl, local) for vision tasks.

    Uses the OpenAI-compatible chat completions API with image_url content.
    Returns the text response, or None on failure.
    """
    data_uri = image_b64 if image_b64.startswith("data:") else f"data:image/jpeg;base64,{image_b64}"
    payload = {
        "model": settings.DMR_VISION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            },
        ],
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }
    # DMR may need 300s for cold-start (model load from disk to VRAM).
    async with httpx.AsyncClient(timeout=300.0) as client:
        try:
            resp = await client.post(
                f"{settings.DMR_URL}/chat/completions",
                headers={"Content-Type": "application/json"},
                json=payload,
            )
            if resp.status_code == 200:
                msg = resp.json()["choices"][0]["message"]
                return msg.get("content") or msg.get("reasoning_content") or ""
            logger.warning("DMR vision returned %s", resp.status_code)
        except Exception as exc:
            logger.warning("DMR vision failed: %s", exc)
    return None


async def _caption_image(image_b64: str) -> str | None:
    """Generate a one-sentence caption. DMR first, then Cloudflare."""
    # --- DMR (local, free) ---
    try:
        text = await _call_dmr_vision(image_b64, "Generate a concise one-sentence caption for this image.")
        if text:
            return text
    except Exception as exc:
        logger.warning("DMR caption failed: %s", exc)

    # --- Cloudflare ---
    try:
        result = await _call_cloudflare_vision(image_b64, "caption", "Generate a concise caption for this image")
        return _extract_caption(result)
    except Exception as exc:
        logger.warning("Cloudflare caption failed: %s", exc)
    return None


async def _tag_image(image_b64: str) -> list[str]:
    """Generate a comma-separated list of keywords/tags. DMR first, then CF."""
    prompt = (
        "List 5 to 10 relevant keywords for this image as a comma-separated list. "
        "Use only single words or short phrases."
    )

    # --- DMR (local, free) ---
    text = ""
    try:
        text = await _call_dmr_vision(image_b64, prompt, max_tokens=200) or ""
    except Exception as exc:
        logger.warning("DMR tag query failed: %s", exc)

    # --- Cloudflare ---
    if not text:
        try:
            result = await _call_cloudflare_vision(image_b64, "query", prompt)
            text = _extract_query_text(result) or ""
        except Exception as exc:
            logger.warning("Cloudflare tag query failed: %s", exc)

    if not text:
        return []

    tags = [t.strip(" -\"'").lower() for t in text.split(",") if t.strip()]
    # Remove obvious instruction remnants
    tags = [t for t in tags if t and not t.startswith("list") and not t.startswith("image")]
    return tags


def _resize_for_vision(image_bytes: bytes, mime_type: str | None = None, max_edge: int = 768) -> tuple[bytes, str]:
    """Downscale large images before sending them to a vision model.

    Preserves the original format when possible (PNG/JPEG/WebP). Falls back to
    PNG if the format cannot be determined or saved.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
        w, h = img.size
        if max(w, h) > max_edge:
            scale = max_edge / float(max(w, h))
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)

        fmt = (img.format or "").upper()
        if mime_type:
            inferred_fmt = mime_type.split("/")[-1].split("+")[0].upper()
            if inferred_fmt:
                fmt = inferred_fmt

        if fmt == "JPEG" or fmt == "JPG" or fmt == "WEBP":
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.save(buf := io.BytesIO(), format=fmt, quality=85, optimize=True)
            return buf.getvalue(), "image/jpeg" if fmt in ("JPEG", "JPG") else "image/webp"

        # Default to PNG for everything else (PNG, GIF, AVIF, HEIC fallback, ...)
        if img.mode == "P":
            img = img.convert("RGBA")
        img.save(buf := io.BytesIO(), format="PNG", optimize=True)
        return buf.getvalue(), "image/png"
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
