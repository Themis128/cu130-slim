"""Image transformation service — platform presets, resize, crop, format conversion, watermark.

All operations use Pillow locally (no AI inference needed). This keeps them
free, fast, and unlimited. Cloudflare-first principle is preserved by using
Cloudflare Images transforms when available in the future, but Pillow is the
reliable local fallback.
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# ── Platform-specific presets ─────────────────────────────────────────────────
# Each preset defines the exact pixel dimensions for optimal display on the
# target platform. These are based on 2025-2026 platform documentation.

PLATFORM_PRESETS: dict[str, dict] = {
    # Instagram
    "instagram_square": {"width": 1080, "height": 1080, "label": "Instagram Square (1:1)"},
    "instagram_portrait": {"width": 1080, "height": 1350, "label": "Instagram Portrait (4:5)"},
    "instagram_landscape": {"width": 1080, "height": 566, "label": "Instagram Landscape (1.91:1)"},
    "instagram_story": {"width": 1080, "height": 1920, "label": "Instagram Story/Reel (9:16)"},
    # LinkedIn
    "linkedin_post": {"width": 1200, "height": 627, "label": "LinkedIn Post (1.91:1)"},
    "linkedin_carousel": {"width": 1080, "height": 1080, "label": "LinkedIn Carousel (1:1)"},
    "linkedin_cover": {"width": 1584, "height": 396, "label": "LinkedIn Cover (4:1)"},
    # Twitter / X
    "twitter_post": {"width": 1200, "height": 675, "label": "Twitter/X Post (16:9)"},
    "twitter_card": {"width": 1200, "height": 628, "label": "Twitter/X Card (1.91:1)"},
    "twitter_header": {"width": 1500, "height": 500, "label": "Twitter/X Header (3:1)"},
    # Facebook
    "facebook_post": {"width": 1200, "height": 630, "label": "Facebook Post (1.91:1)"},
    "facebook_cover": {"width": 820, "height": 312, "label": "Facebook Cover (2.62:1)"},
    "facebook_story": {"width": 1080, "height": 1920, "label": "Facebook Story (9:16)"},
    # TikTok
    "tiktok_cover": {"width": 1080, "height": 1920, "label": "TikTok Cover (9:16)"},
    # Threads
    "threads_post": {"width": 1080, "height": 1080, "label": "Threads Post (1:1)"},
    # Open Graph / SEO
    "og_image": {"width": 1200, "height": 630, "label": "Open Graph Image (1.91:1)"},
    # Generic
    "square_1080": {"width": 1080, "height": 1080, "label": "Square 1080px"},
    "square_2048": {"width": 2048, "height": 2048, "label": "Square 2048px"},
}


@dataclass
class TransformResult:
    """Result of an image transformation."""
    image_bytes: bytes
    mime_type: str
    width: int
    height: int
    format: str


def _open_image(image_bytes: bytes) -> Image.Image:
    """Open an image from bytes, handling format conversion."""
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode == "P":
        img = img.convert("RGBA")
    elif img.mode == "LA":
        img = img.convert("RGBA")
    return img


def _save_image(img: Image.Image, format: str = "jpeg", quality: int = 85) -> tuple[bytes, str]:
    """Save image to bytes in the specified format."""
    format_upper = format.upper()
    if format_upper in ("JPG", "JPEG"):
        if img.mode in ("RGBA", "P"):
            # Composite onto white background for JPEG
            bg = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "RGBA":
                bg.paste(img, mask=img.split()[3])
            else:
                bg.paste(img)
            img = bg
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        return buf.getvalue(), "image/jpeg"
    elif format_upper == "PNG":
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue(), "image/png"
    elif format_upper == "WEBP":
        if img.mode == "P":
            img = img.convert("RGBA")
        buf = io.BytesIO()
        img.save(buf, format="WEBP", quality=quality, method=6)
        return buf.getvalue(), "image/webp"
    elif format_upper == "AVIF":
        try:
            buf = io.BytesIO()
            img.save(buf, format="AVIF", quality=quality)
            return buf.getvalue(), "image/avif"
        except Exception:
            # AVIF not available, fall back to WebP
            logger.warning("AVIF format not available, falling back to WebP")
            return _save_image(img, "webp", quality)
    else:
        raise ValueError(f"Unsupported format: {format}")


def resize_image(
    image_bytes: bytes,
    width: int | None = None,
    height: int | None = None,
    preset: str | None = None,
    fit: str = "cover",
    format: str = "jpeg",
    quality: int = 85,
) -> TransformResult:
    """Resize an image to specific dimensions or a platform preset.

    Args:
        image_bytes: Raw image bytes.
        width: Target width (ignored if preset is set).
        height: Target height (ignored if preset is set).
        preset: Platform preset key from PLATFORM_PRESETS.
        fit: "cover" (crop to fill) or "contain" (fit within, may letterbox).
        format: Output format (jpeg, png, webp, avif).
        quality: JPEG/WebP quality (1-100).
    """
    if preset:
        if preset not in PLATFORM_PRESETS:
            raise ValueError(f"Unknown preset: {preset}. Available: {list(PLATFORM_PRESETS.keys())}")
        p = PLATFORM_PRESETS[preset]
        target_w, target_h = p["width"], p["height"]
    else:
        if not width and not height:
            raise ValueError("Either width, height, or preset must be specified")
        img = _open_image(image_bytes)
        orig_w, orig_h = img.size
        if width and not height:
            # Maintain aspect ratio
            height = int(orig_h * width / orig_w)
        elif height and not width:
            width = int(orig_w * height / orig_h)
        target_w, target_h = width, height

    img = _open_image(image_bytes)
    orig_w, orig_h = img.size

    if fit == "cover":
        # Scale to fill, then crop center
        scale = max(target_w / orig_w, target_h / orig_h)
        new_w = int(orig_w * scale)
        new_h = int(orig_h * scale)
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        # Center crop
        left = (new_w - target_w) // 2
        top = (new_h - target_h) // 2
        img = img.crop((left, top, left + target_w, top + target_h))
    elif fit == "contain":
        # Scale to fit within, pad with background
        scale = min(target_w / orig_w, target_h / orig_h)
        new_w = int(orig_w * scale)
        new_h = int(orig_h * scale)
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        # Create canvas with target dimensions
        canvas = Image.new("RGBA", (target_w, target_h), (255, 255, 255, 0))
        offset = ((target_w - new_w) // 2, (target_h - new_h) // 2)
        canvas.paste(img, offset, img if img.mode == "RGBA" else None)
        img = canvas

    out_bytes, mime = _save_image(img, format, quality)
    return TransformResult(out_bytes, mime, target_w, target_h, format)


def crop_image(
    image_bytes: bytes,
    x: int,
    y: int,
    width: int,
    height: int,
    format: str = "jpeg",
    quality: int = 85,
) -> TransformResult:
    """Crop image to specific region."""
    img = _open_image(image_bytes)
    img = img.crop((x, y, x + width, y + height))
    out_bytes, mime = _save_image(img, format, quality)
    w, h = img.size
    return TransformResult(out_bytes, mime, w, h, format)


def convert_format(
    image_bytes: bytes,
    format: str = "webp",
    quality: int = 85,
) -> TransformResult:
    """Convert image to a different format."""
    img = _open_image(image_bytes)
    out_bytes, mime = _save_image(img, format, quality)
    w, h = img.size
    return TransformResult(out_bytes, mime, w, h, format)


def compress_image(
    image_bytes: bytes,
    target_size_kb: int = 500,
    format: str = "jpeg",
    min_quality: int = 30,
) -> TransformResult:
    """Compress image to target file size (in KB) by iteratively reducing quality."""
    img = _open_image(image_bytes)
    quality = 90
    out_bytes, mime = _save_image(img, format, quality)
    while len(out_bytes) > target_size_kb * 1024 and quality > min_quality:
        quality -= 5
        out_bytes, mime = _save_image(img, format, quality)
    w, h = img.size
    return TransformResult(out_bytes, mime, w, h, format)


def add_watermark(
    image_bytes: bytes,
    text: str,
    position: str = "bottom-right",
    opacity: int = 128,
    font_size: int = 36,
    color: tuple = (255, 255, 255),
    format: str = "jpeg",
    quality: int = 85,
) -> TransformResult:
    """Add a text watermark to an image.

    Args:
        text: Watermark text.
        position: One of top-left, top-right, bottom-left, bottom-right, center.
        opacity: 0-255 text opacity.
        font_size: Font size in pixels.
        color: RGB text color.
    """
    img = _open_image(image_bytes)
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    # Create overlay
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Try to load a font, fall back to default
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()

    # Calculate text size
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    padding = 20

    positions = {
        "top-left": (padding, padding),
        "top-right": (img.size[0] - text_w - padding, padding),
        "bottom-left": (padding, img.size[1] - text_h - padding),
        "bottom-right": (img.size[0] - text_w - padding, img.size[1] - text_h - padding),
        "center": ((img.size[0] - text_w) // 2, (img.size[1] - text_h) // 2),
    }

    x, y = positions.get(position, positions["bottom-right"])
    draw.text((x, y), text, fill=(*color, opacity), font=font)

    # Composite
    img = Image.alpha_composite(img, overlay)
    out_bytes, mime = _save_image(img, format, quality)
    w, h = img.size
    return TransformResult(out_bytes, mime, w, h, format)


def get_image_info(image_bytes: bytes) -> dict:
    """Get basic image info without transforming."""
    img = _open_image(image_bytes)
    return {
        "width": img.size[0],
        "height": img.size[1],
        "mode": img.mode,
        "format": img.format or "unknown",
    }
