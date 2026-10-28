"""Brand footer overlay for generated post media.

Every AI-generated image that flows into the media library gets a slim
brand footer: an accent hairline over a dark strip with the brand name on
the left and the brand domain on the right — same visual language as the
carousel slides. Sources that are already fully branded (carousels, ad
creatives) or must stay clean (logos, favicons) are exempt via
``FOOTER_EXEMPT_SOURCES``. Set ``BRAND_FOOTER_DISABLED=1`` to opt out.
"""

from __future__ import annotations

import io
import logging
import os
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

logger = logging.getLogger(__name__)

# Sources that must NOT get the footer — already branded or brand assets
# themselves (a logo with a footer baked in is unusable).
FOOTER_EXEMPT_SOURCES = frozenset(
    {
        "ai-logo",
        "ai-favicon",
        "carousel",
        "comfyui-carousel",
        "n8n-cf-pipe",
        "cf-carousel-pipeline",
        "ad-creative",
    }
)

_MIN_EDGE = 480  # never stamp tiny images (thumbnails, icons)

# Brand tokens — same palette as the carousel composer.
_BG = (15, 15, 23)
_TEXT = (235, 235, 245)
_DEFAULT_ACCENT = (0, 255, 245)

_FONT_DIR = "/app/app/assets/fonts"
_FONT_FILES = {
    "bold": os.path.join(_FONT_DIR, "WorkSans-Bold.ttf"),
    "semibold": os.path.join(_FONT_DIR, "WorkSans-SemiBold.ttf"),
    "regular": os.path.join(_FONT_DIR, "WorkSans-Regular.ttf"),
}
_FALLBACK_FONTS = {
    "bold": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "regular": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
}


def _font(size: int, weight: str = "regular") -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = _FONT_FILES.get(weight, _FONT_FILES["regular"])
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        try:
            return ImageFont.truetype(_FALLBACK_FONTS.get(weight, _FALLBACK_FONTS["regular"]), size)
        except Exception:
            return ImageFont.load_default()


def _hex_rgb(hex_color: str | None) -> tuple[int, int, int] | None:
    if not hex_color:
        return None
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (ValueError, IndexError):
        return None


async def load_footer_brand(db: AsyncSession, team_id) -> dict | None:
    """Load the team's brand tokens for the footer overlay.

    Returns ``{"name", "domain", "accent"}`` or None when no brand exists.
    """
    from app.models.brand import Brand

    if not team_id:
        return None
    result = await db.execute(select(Brand).options(selectinload(Brand.visual)).where(Brand.team_id == team_id))
    brand = result.scalars().first()
    if not brand:
        return None
    visual = brand.visual

    domain = ""
    if brand.website_url:
        domain = urlparse(brand.website_url).netloc or brand.website_url
        domain = domain.removeprefix("www.")
    if not domain:
        domain = brand.tagline or ""

    return {
        "name": brand.name or "",
        "domain": domain,
        "accent": _hex_rgb(visual.accent_color if visual else None) or _DEFAULT_ACCENT,
    }


def apply_brand_footer(image_bytes: bytes, brand: dict) -> bytes:
    """Overlay a slim brand footer strip on the image. Returns PNG bytes.

    Never raises for branding purposes — callers should wrap in try/except
    anyway so a footer failure can never break image persistence.
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    w, h = img.size
    if w < _MIN_EDGE or h < _MIN_EDGE:
        return image_bytes

    bar_h = max(44, round(h * 0.0625))
    hairline = max(2, bar_h // 16)
    accent = brand.get("accent") or _DEFAULT_ACCENT

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    top = h - bar_h
    draw.rectangle([0, top, w, h], fill=(*_BG, 205))
    draw.rectangle([0, top, w, top + hairline], fill=(*accent, 235))

    pad = max(20, round(w * 0.035))
    name_size = max(18, round(bar_h * 0.42))
    domain_size = max(16, round(bar_h * 0.36))
    name_font = _font(name_size, "bold")
    domain_font = _font(domain_size, "semibold")

    name = (brand.get("name") or "").strip()
    domain = (brand.get("domain") or "").strip()

    text_y = top + hairline + (bar_h - hairline - name_size) // 2
    if name:
        draw.text((pad, text_y), name, font=name_font, fill=(*accent, 255))
    if domain:
        dw = draw.textlength(domain, font=domain_font)
        draw.text((w - pad - dw, text_y), domain, font=domain_font, fill=(*_TEXT, 235))

    img.alpha_composite(overlay)
    out = io.BytesIO()
    img.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()


async def maybe_apply_brand_footer(
    db: AsyncSession,
    team_id,
    image_bytes: bytes,
    source: str,
) -> bytes:
    """Apply the brand footer unless disabled or the source is exempt."""
    if os.environ.get("BRAND_FOOTER_DISABLED"):
        return image_bytes
    if source in FOOTER_EXEMPT_SOURCES:
        return image_bytes
    try:
        brand = await load_footer_brand(db, team_id)
        if not brand or not brand.get("name"):
            return image_bytes
        return apply_brand_footer(image_bytes, brand)
    except Exception as exc:  # noqa: BLE001 — branding must never break persistence
        logger.warning("Brand footer skipped for %s asset: %s", source, exc)
        return image_bytes
