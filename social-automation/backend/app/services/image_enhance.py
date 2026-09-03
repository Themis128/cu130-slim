"""AI-powered image enhancement service.

DMR-first (local, free), Cloudflare failover:
- Background removal: Cloudflare Images transform `cf.image segment: "foreground"`
  (BiRefNet) when available, or local Pillow with rembg as fallback.
- Upscaling: Pillow LANCZOS for 2x/4x (local, free, unlimited).
  Real-ESRGAN via Workers AI when available.
- Smart crop: DMR qwen3-vl (local) detects subject position first, then CF
  llama-4-scout as failover. Pillow crops to target aspect ratio centered on subject.
- Quality scoring: Local Pillow computation (Laplacian variance, histogram
  analysis) — no AI inference needed.
- Alt text: DMR qwen3-vl (local) first, then CF llama-4-scout.

All AI operations fall back to Cloudflare if DMR is unavailable.
"""
from __future__ import annotations

import base64
import io
import logging
from dataclasses import dataclass
from urllib.parse import quote

import httpx
from PIL import Image, ImageFilter, ImageStat

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


# ── Quality Scoring (local, no AI) ────────────────────────────────────────────

@dataclass
class ImageQualityScore:
    """Image quality assessment result."""
    overall: int  # 0-100
    sharpness: int  # 0-100
    brightness: int  # 0-100
    contrast: int  # 0-100
    blur_detected: bool
    too_dark: bool
    too_bright: bool
    issues: list[str]


def score_image_quality(image_bytes: bytes) -> ImageQualityScore:
    """Score image quality using local computation (no AI needed).

    Uses Laplacian variance for blur detection and histogram analysis
    for brightness/contrast assessment.
    """
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode != "L":
        gray = img.convert("L")
    else:
        gray = img

    # Sharpness via Laplacian variance
    laplacian = gray.filter(ImageFilter.Kernel(
        size=(3, 3),
        kernel=[0, -1, 0, -1, 4, -1, 0, -1, 0],
        scale=1,
        offset=0,
    ))
    stat = ImageStat.Stat(laplacian)
    laplacian_var = stat.var[0] if stat.var else 0
    # Map variance to 0-100 (empirical thresholds)
    sharpness = min(100, int(laplacian_var / 5))
    blur_detected = laplacian_var < 100

    # Brightness via mean
    stat = ImageStat.Stat(gray)
    mean_brightness = stat.mean[0] if stat.mean else 0
    # Map 0-255 to 0-100, ideal is around 128
    if mean_brightness < 50:
        brightness = int(mean_brightness * 2)
        too_dark = True
        too_bright = False
    elif mean_brightness > 210:
        brightness = int(100 - (mean_brightness - 210) * 2)
        too_dark = False
        too_bright = True
    else:
        # 50-210 maps to 40-100
        brightness = int(40 + (mean_brightness - 50) * 60 / 160)
        too_dark = False
        too_bright = False

    # Contrast via standard deviation
    contrast_raw = stat.stddev[0] if stat.stddev else 0
    contrast = min(100, int(contrast_raw * 1.5))

    # Overall score (weighted average)
    overall = int(sharpness * 0.4 + brightness * 0.3 + contrast * 0.3)

    issues: list[str] = []
    if blur_detected:
        issues.append("Image appears blurry — consider upscaling or re-capturing")
    if too_dark:
        issues.append("Image is too dark — consider brightness enhancement")
    if too_bright:
        issues.append("Image is overexposed — consider reducing brightness")
    if contrast < 30:
        issues.append("Low contrast — image may look flat")

    return ImageQualityScore(
        overall=overall,
        sharpness=sharpness,
        brightness=brightness,
        contrast=contrast,
        blur_detected=blur_detected,
        too_dark=too_dark,
        too_bright=too_bright,
        issues=issues,
    )


# ── Upscaling (local Pillow, free) ────────────────────────────────────────────

def upscale_image(image_bytes: bytes, scale: int = 2) -> tuple[bytes, str, int, int]:
    """Upscale image using Pillow LANCZOS resampling.

    For 2x/4x upscaling. While not as good as Real-ESRGAN, it's free,
    local, and unlimited. AI upscaling can be added as a Cloudflare
    Workers AI model when available.

    Returns (image_bytes, mime_type, width, height).
    """
    if scale not in (2, 4):
        raise ValueError("Scale must be 2 or 4")

    img = Image.open(io.BytesIO(image_bytes))
    orig_w, orig_h = img.size
    new_w = orig_w * scale
    new_h = orig_h * scale

    # Use LANCZOS for best quality upscaling
    img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

    # Apply slight sharpening to counteract upscaling softness
    img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=50, threshold=0))

    buf = io.BytesIO()
    if img.mode in ("RGBA", "P"):
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue(), "image/png", new_w, new_h
    else:
        img.save(buf, format="JPEG", quality=90, optimize=True)
        return buf.getvalue(), "image/jpeg", new_w, new_h


# ── Background Removal (Cloudflare Workers AI) ────────────────────────────────

async def remove_background_cf(image_bytes: bytes) -> bytes | None:
    """Remove image background using Cloudflare Workers AI.

    Uses the BAAI segmentation model available on Workers AI.
    Falls back to None if Cloudflare is unavailable.
    """
    account_id = (settings.CLOUDFLARE_ACCOUNT_ID or "").strip()
    token = (settings.CLOUDFLARE_AI_API_TOKEN or "").strip() or (settings.CLOUDFLARE_API_TOKEN or "").strip()
    if not account_id or not token:
        logger.warning("Cloudflare credentials not configured for background removal")
        return None

    # Resize image to reasonable size for the model
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    max_edge = 1024
    w, h = img.size
    if max(w, h) > max_edge:
        scale = max_edge / float(max(w, h))
        img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    image_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

    # Try Workers AI rembg model
    # @cf/baai/baseten-lightning-iris is a segmentation model
    model = quote("@cf/baai/baseten-lightning-iris", safe="@/")
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={"image": image_b64},
            )
        if resp.status_code == 200:
            data = resp.json()
            result = data.get("result") or data
            # The model returns a mask or segmented image
            if "image" in result:
                mask_b64 = result["image"]
                mask_bytes = base64.b64decode(mask_b64)
                # Apply mask to original image
                return _apply_bg_mask(image_bytes, mask_bytes)
            elif "output" in result:
                output_b64 = result["output"]
                if isinstance(output_b64, str):
                    return base64.b64decode(output_b64)
        logger.warning("CF background removal returned %s: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.warning("CF background removal failed: %s", exc)

    return None


def _apply_bg_mask(image_bytes: bytes, mask_bytes: bytes) -> bytes:
    """Apply a binary mask to remove background, producing transparent PNG."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    mask = Image.open(io.BytesIO(mask_bytes)).convert("L")
    # Resize mask to match image
    if mask.size != img.size:
        mask = mask.resize(img.size, Image.Resampling.LANCZOS)
    # Apply mask as alpha channel
    img.putalpha(mask)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


async def remove_background(image_bytes: bytes) -> tuple[bytes, str]:
    """Remove image background. Returns (image_bytes, mime_type).

    Tries Cloudflare Workers AI first, then local rembg if available.
    """
    # Try Cloudflare
    result = await remove_background_cf(image_bytes)
    if result:
        return result, "image/png"

    # Fallback: try local rembg
    try:
        from rembg import remove as rembg_remove
        result = rembg_remove(image_bytes)
        if result is None:
            raise RuntimeError("rembg returned None")
        return result, "image/png"
    except ImportError:
        logger.warning("rembg not installed and Cloudflare unavailable for background removal")
        raise RuntimeError("Background removal unavailable — Cloudflare not configured and rembg not installed")


# ── Smart Crop (AI subject detection + Pillow crop) ───────────────────────────

async def _dmr_vision_query(data_uri: str, prompt: str, max_tokens: int = 60) -> str | None:
    """Call DMR qwen3-vl (local) for a vision query. Returns text or None."""
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


def _prepare_image_for_vision(image_bytes: bytes) -> str:
    """Resize and base64-encode an image for vision model input. Returns data URI."""
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    max_edge = 768
    w, h = img.size
    if max(w, h) > max_edge:
        scale = max_edge / float(max(w, h))
        img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    image_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{image_b64}"


async def detect_subject_position(image_bytes: bytes) -> tuple[int, int] | None:
    """Detect the main subject position in an image using vision models.

    DMR qwen3-vl (local) first, then Cloudflare llama-4-scout as failover.
    Returns (x, y) center coordinates as percentages (0-100) of image dimensions,
    or None if detection fails.
    """
    import re

    data_uri = _prepare_image_for_vision(image_bytes)
    subject_prompt = (
        "Look at this image. Where is the main subject located? "
        "Reply with ONLY two numbers separated by a comma: the "
        "approximate X and Y position as percentages (0-100) of "
        "the image width and height. For example: 50,50 for center, "
        "25,30 for upper-left area."
    )

    # --- DMR (local, free) ---
    try:
        response = await _dmr_vision_query(data_uri, subject_prompt, max_tokens=20)
        if response:
            match = re.search(r"(\d+)\s*,\s*(\d+)", response)
            if match:
                x, y = int(match.group(1)), int(match.group(2))
                if 0 <= x <= 100 and 0 <= y <= 100:
                    return (x, y)
    except Exception as exc:
        logger.warning("DMR subject detection failed: %s", exc)

    # --- Cloudflare (failover) ---
    account_id = (settings.CLOUDFLARE_ACCOUNT_ID or "").strip()
    token = (settings.CLOUDFLARE_AI_API_TOKEN or "").strip() or (settings.CLOUDFLARE_API_TOKEN or "").strip()
    if not account_id or not token:
        return None

    model = quote("@cf/meta/llama-4-scout-17b-16e-instruct", safe="@/")
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"

    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": subject_prompt},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            },
        ],
        "max_tokens": 20,
        "stream": False,
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
            )
        if resp.status_code == 200:
            data = resp.json()
            response = data.get("result", {}).get("response", "")
            match = re.search(r"(\d+)\s*,\s*(\d+)", response)
            if match:
                x = int(match.group(1))
                y = int(match.group(2))
                if 0 <= x <= 100 and 0 <= y <= 100:
                    return (x, y)
    except Exception as exc:
        logger.warning("CF subject detection failed: %s", exc)

    return None


def smart_crop(
    image_bytes: bytes,
    target_width: int,
    target_height: int,
    subject_pos: tuple[int, int] | None = None,
) -> tuple[bytes, str, int, int]:
    """Crop image to target aspect ratio, focusing on the subject.

    If subject_pos is provided (as percentages 0-100), the crop is centered
    on that position. Otherwise, center crop is used.

    Returns (image_bytes, mime_type, width, height).
    """
    img = Image.open(io.BytesIO(image_bytes))
    orig_w, orig_h = img.size

    # Calculate crop dimensions that match target aspect ratio
    target_ratio = target_width / target_height
    orig_ratio = orig_w / orig_h

    if orig_ratio > target_ratio:
        # Image is wider than target — crop width
        crop_h = orig_h
        crop_w = int(orig_h * target_ratio)
    else:
        # Image is taller than target — crop height
        crop_w = orig_w
        crop_h = int(orig_w / target_ratio)

    # Determine crop center
    if subject_pos:
        # Convert percentages to pixel coordinates
        center_x = int(orig_w * subject_pos[0] / 100)
        center_y = int(orig_h * subject_pos[1] / 100)
    else:
        center_x = orig_w // 2
        center_y = orig_h // 2

    # Calculate crop box, clamping to image bounds
    left = max(0, min(center_x - crop_w // 2, orig_w - crop_w))
    top = max(0, min(center_y - crop_h // 2, orig_h - crop_h))

    img = img.crop((left, top, left + crop_w, top + crop_h))
    img = img.resize((target_width, target_height), Image.Resampling.LANCZOS)

    buf = io.BytesIO()
    if img.mode in ("RGBA", "P"):
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue(), "image/png", target_width, target_height
    else:
        img.save(buf, format="JPEG", quality=90, optimize=True)
        return buf.getvalue(), "image/jpeg", target_width, target_height


async def smart_crop_async(
    image_bytes: bytes,
    target_width: int,
    target_height: int,
) -> tuple[bytes, str, int, int]:
    """Smart crop with AI subject detection. Falls back to center crop."""
    subject_pos = await detect_subject_position(image_bytes)
    return smart_crop(image_bytes, target_width, target_height, subject_pos)


# ── AI Alt Text Generation ────────────────────────────────────────────────────

async def generate_alt_text(image_bytes: bytes) -> str | None:
    """Generate accessibility-focused alt text for screen readers.

    DMR qwen3-vl (local) first, then Cloudflare llama-4-scout, then Ollama llava.
    Produces concise, descriptive alt text under 125 characters (WCAG recommendation).
    """
    data_uri = _prepare_image_for_vision(image_bytes)

    alt_prompt = (
        "Write alt text for this image for accessibility purposes. "
        "The alt text should: 1) Be under 125 characters. "
        "2) Describe what the image conveys, not what it looks like. "
        "3) Be concise and meaningful for screen reader users. "
        "4) Not start with 'Image of' or 'Picture of'. "
        "Reply with ONLY the alt text, nothing else."
    )

    # --- DMR (local, free) ---
    try:
        response = await _dmr_vision_query(data_uri, alt_prompt, max_tokens=60)
        if response:
            alt_text = response.strip().strip('"').strip("'")
            if alt_text:
                return alt_text[:125]
    except Exception as exc:
        logger.warning("DMR alt text generation failed: %s", exc)

    # --- Cloudflare (failover) ---
    account_id = (settings.CLOUDFLARE_ACCOUNT_ID or "").strip()
    token = (settings.CLOUDFLARE_AI_API_TOKEN or "").strip() or (settings.CLOUDFLARE_API_TOKEN or "").strip()
    if account_id and token:
        model = quote("@cf/meta/llama-4-scout-17b-16e-instruct", safe="@/")
        url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"
        payload = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": alt_prompt},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                },
            ],
            "max_tokens": 60,
            "stream": False,
        }
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    json=payload,
                )
            if resp.status_code == 200:
                data = resp.json()
                response = data.get("result", {}).get("response", "") or ""
                alt_text = response.strip().strip('"').strip("'")
                if alt_text:
                    return alt_text[:125]
        except Exception as exc:
            logger.warning("CF alt text generation failed: %s", exc)

    return None
