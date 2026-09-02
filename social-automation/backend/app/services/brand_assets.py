"""Brand asset generation service.

Generates launch assets (OG images, social banners, favicons) from brand
visual identity. Uses Cloudflare Workers AI for image generation and
Pillow for compositing.
"""

from __future__ import annotations

import io
from typing import Any

from PIL import Image, ImageDraw, ImageFont


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert hex color string to RGB tuple."""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 3:
        hex_color = "".join(c * 2 for c in hex_color)
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


def generate_og_image(
    brand_name: str,
    tagline: str | None = None,
    primary_color: str = "#0f0f17",
    accent_color: str = "#00fff5",
    width: int = 1200,
    height: int = 630,
) -> bytes:
    """Generate an Open Graph image from brand colors and name.

    Returns PNG bytes suitable for upload to the media library.
    """
    bg = _hex_to_rgb(primary_color)
    accent = _hex_to_rgb(accent_color)

    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)

    # Accent bar at top
    draw.rectangle([0, 0, width, 6], fill=accent)

    # Brand name centered
    try:
        font_large = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 72
        )
        font_small = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 32
        )
    except OSError:
        font_large = ImageFont.load_default()
        font_small = ImageFont.load_default()

    # Draw brand name
    name_w = draw.textlength(brand_name, font=font_large)
    draw.text(
        ((width - name_w) // 2, height // 2 - 60),
        brand_name,
        font=font_large,
        fill=accent,
    )

    # Draw tagline if provided
    if tagline:
        tag_w = draw.textlength(tagline, font=font_small)
        draw.text(
            ((width - tag_w) // 2, height // 2 + 30),
            tagline,
            font=font_small,
            fill=(200, 200, 220),
        )

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def generate_social_banner(
    brand_name: str,
    tagline: str | None = None,
    primary_color: str = "#0f0f17",
    accent_color: str = "#00fff5",
    platform: str = "linkedin",
) -> bytes:
    """Generate a social media banner (cover photo).

    Platform-specific dimensions:
    - LinkedIn: 1584x396
    - Twitter: 1500x500
    - Facebook: 820x312
    """
    dimensions = {
        "linkedin": (1584, 396),
        "twitter": (1500, 500),
        "facebook": (820, 312),
    }
    width, height = dimensions.get(platform, (1500, 500))

    bg = _hex_to_rgb(primary_color)
    accent = _hex_to_rgb(accent_color)

    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)

    # Left accent bar
    draw.rectangle([0, 0, 8, height], fill=accent)

    try:
        font_large = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 48
        )
        font_small = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24
        )
    except OSError:
        font_large = ImageFont.load_default()
        font_small = ImageFont.load_default()

    # Brand name left-aligned with padding
    draw.text((60, height // 2 - 40), brand_name, font=font_large, fill=accent)

    if tagline:
        draw.text((60, height // 2 + 20), tagline, font=font_small, fill=(200, 200, 220))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def build_brand_image_prompt(
    brand_visual: dict[str, Any],
    brand_voice: dict[str, Any] | None = None,
    base_prompt: str = "",
) -> str:
    """Build an AI image generation prompt infused with brand visual identity.

    Args:
        brand_visual: BrandVisual dict with colors, fonts, image_style, etc.
        brand_voice: Optional BrandVoice dict for tone-aware prompts.
        base_prompt: The user's base prompt to enhance with brand context.

    Returns:
        Enhanced prompt string with brand visual direction.
    """
    parts: list[str] = []

    if base_prompt:
        parts.append(base_prompt)

    # Add image style direction
    image_style = brand_visual.get("image_style")
    if image_style:
        parts.append(f"Style: {image_style}")

    # Add photography direction
    photo_dir = brand_visual.get("photography_direction")
    if photo_dir:
        parts.append(f"Photography: {photo_dir}")

    # Add color palette
    colors: list[str] = []
    if brand_visual.get("primary_color"):
        colors.append(brand_visual["primary_color"])
    if brand_visual.get("accent_color"):
        colors.append(brand_visual["accent_color"])
    if brand_visual.get("neutral_colors"):
        colors.extend(brand_visual["neutral_colors"])
    if colors:
        parts.append(f"Color palette: {', '.join(colors)}")

    # Add tone from voice
    if brand_voice:
        tone_dims = brand_voice.get("tone_dimensions", {})
        if tone_dims:
            tone_descs: list[str] = []
            tone_map = {
                "formality": ("formal", "casual"),
                "playfulness": ("serious", "playful"),
                "authority": ("humble", "authoritative"),
                "friendliness": ("distant", "friendly"),
                "technical": ("simple", "technical"),
            }
            for key, (low, high) in tone_map.items():
                val = tone_dims.get(key, 3)
                if val >= 4:
                    tone_descs.append(high)
                elif val <= 2:
                    tone_descs.append(low)
            if tone_descs:
                parts.append(f"Mood: {', '.join(tone_descs)}")

    return ". ".join(parts) + "." if parts else base_prompt
