"""Infographic renderer — generates text-free backgrounds via AI, then overlays
correctly-spelled text via PIL.

AI image models (SD 1.5, FLUX) cannot spell. When a user asks for an infographic,
poster, or any text-heavy visual, this service:

1. Detects the request type (infographic, poster, chart, etc.)
2. Generates structured text content via Cloudflare Workers AI (LLM)
3. Generates a clean **text-free** background image via the normal image pipeline
4. Overlays the structured text using PIL with proper fonts
5. Returns the composited image with perfect spelling

This mirrors the approach used by ``carousel_pipeline.py`` but for single-image
infographics rather than multi-slide PDFs.
"""

from __future__ import annotations

import io
import logging
import os
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from app.services.inference import call_inference

logger = logging.getLogger(__name__)

# ── Brand tokens (same as carousel_pipeline) ──────────────────────────────────
BG = (15, 15, 23)          # #0f0f17
CARD = (22, 22, 34)        # slightly lighter panel
ACCENT = (0, 255, 245)     # #00fff5 cloudless cyan
ACCENT2 = (255, 100, 40)   # warm orange accent
TEXT_COL = (225, 235, 245)  # near-white body copy
SUB = (120, 135, 160)      # muted blue-grey
GRID = (30, 30, 45)        # subtle grid line colour

_FONT_DIR = "/app/app/assets/fonts"
_FONT_FILES = {
    "bold": os.path.join(_FONT_DIR, "WorkSans-Bold.ttf"),
    "semibold": os.path.join(_FONT_DIR, "WorkSans-SemiBold.ttf"),
    "regular": os.path.join(_FONT_DIR, "WorkSans-Regular.ttf"),
}
_FALLBACK = {
    "bold": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "regular": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
}

# Keywords that trigger the infographic renderer
_INFOGRAPHIC_KEYWORDS = {
    "infographic", "poster", "flyer", "brochure", "chart",
    "diagram", "statistics", "stats", "data visualization",
    "report", "summary", "overview", "factsheet", "fact sheet",
    "comparison", "breakdown", "timeline", "process", "steps",
    "guide", "checklist", "tips", "how-to", "how to",
}


def _font(size: int, weight: str = "regular") -> ImageFont.FreeTypeFont:
    path = _FONT_FILES.get(weight, _FONT_FILES["regular"])
    if os.path.exists(path):
        return ImageFont.truetype(path, size)
    fb = _FALLBACK.get("bold" if weight == "bold" else "regular", _FALLBACK["regular"])
    if os.path.exists(fb):
        return ImageFont.truetype(fb, size)
    return ImageFont.load_default()


def _ascii_safe(text: str) -> str:
    """Replace fancy dashes/quotes so fonts never show tofu glyphs."""
    return (
        (text or "")
        .replace("\u2014", " - ")
        .replace("\u2013", "-")
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2022", "-")
        .strip()
    )


def is_infographic_request(prompt: str) -> bool:
    """Check if the prompt is asking for an infographic or text-heavy visual."""
    prompt_lower = prompt.lower()
    return any(kw in prompt_lower for kw in _INFOGRAPHIC_KEYWORDS)


def sanitize_prompt_for_background(prompt: str) -> str:
    """Modify a prompt to tell the AI model NOT to render text.

    AI image models garble text. We ask for a clean background with space
    for text overlay, then render the text ourselves via PIL.
    """
    # Remove explicit text requests and add anti-text instructions
    return (
        f"{prompt}, clean minimalist background design with large empty spaces "
        f"for text overlay, NO TEXT, NO WORDS, NO LETTERS, NO WRITING, "
        f"NO TYPOGRAPHY, abstract decorative elements only, "
        f"professional dark blue corporate style"
    )


async def generate_infographic_content(
    prompt: str,
    platform: str = "linkedin",
) -> dict[str, Any]:
    """Generate structured infographic text content via Cloudflare Workers AI.

    Returns JSON with:
    - title: main heading
    - subtitle: optional subheading
    - sections: list of {icon_emoji, heading, body}
    - footer: optional footer text
    """
    llm_prompt = f"""You are an infographic content designer.

Create structured content for an infographic based on this request:
"{prompt}"

Platform: {platform}

Rules:
- Write clear, concise, professional content
- Use plain English (no jargon, no buzzwords)
- Each section heading must be 2-5 words
- Each section body must be 1-2 sentences (under 150 characters)
- Use emoji for section icons (1 emoji each)
- Title must be 3-8 words
- 3-5 sections maximum
- All text must be perfectly spelled

Return JSON with exactly:
- title: string
- subtitle: string (optional, can be empty)
- sections: array of {{icon: string, heading: string, body: string}}
- footer: string (optional, can be empty)
"""

    schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "subtitle": {"type": "string"},
            "sections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "icon": {"type": "string"},
                        "heading": {"type": "string"},
                        "body": {"type": "string"},
                    },
                    "required": ["icon", "heading", "body"],
                },
            },
            "footer": {"type": "string"},
        },
        "required": ["title", "sections"],
    }

    result = await call_inference(
        llm_prompt,
        provider_name="cloudflare",
        schema=schema,
        max_tokens=800,
    )
    return result


def render_infographic(
    content: dict[str, Any],
    background_bytes: bytes,
    width: int = 1024,
    height: int = 1024,
) -> bytes:
    """Composite structured text content over a background image via PIL.

    Args:
        content: Structured content from generate_infographic_content.
        background_bytes: PNG/JPEG bytes of the text-free background.
        width: Output image width.
        height: Output image height.

    Returns:
        PNG bytes of the finished infographic with correctly-spelled text.
    """
    # Load and resize background to target dimensions
    bg = Image.open(io.BytesIO(background_bytes)).convert("RGBA")
    bg = bg.resize((width, height), Image.LANCZOS)

    # Darken the background for text readability (60% opacity dark overlay)
    overlay = Image.new("RGBA", (width, height), (*BG, 180))
    composited = Image.alpha_composite(bg, overlay)
    img = composited.convert("RGB")
    draw = ImageDraw.Draw(img)

    # ── Header ────────────────────────────────────────────────────────────────
    # Gradient accent bar at top
    for x in range(width):
        t = x / max(width - 1, 1)
        color = (
            int(ACCENT[0] * (1 - t) + ACCENT2[0] * t),
            int(ACCENT[1] * (1 - t) + ACCENT2[1] * t),
            int(ACCENT[2] * (1 - t) + ACCENT2[2] * t),
        )
        draw.line([(x, 0), (x, 12)], fill=color)

    # Brand logo
    brand_font = _font(32, "bold")
    dot_font = _font(32, "regular")
    brand_text = "cloudless"
    cw = draw.textlength(brand_text, font=brand_font)
    margin = 60
    draw.text((margin, 36), brand_text, font=brand_font, fill=ACCENT)
    draw.text((margin + cw, 36), ".gr", font=dot_font, fill=SUB)

    # ── Title ─────────────────────────────────────────────────────────────────
    title = _ascii_safe(content.get("title", ""))
    subtitle = _ascii_safe(content.get("subtitle", ""))
    title_font = _font(44, "bold")
    subtitle_font = _font(26, "regular")

    title_y = 110
    # Wrap title if too long
    max_title_width = width - 2 * margin
    title_lines = _wrap_text(draw, title, title_font, max_title_width)
    for line in title_lines:
        draw.text((margin, title_y), line, font=title_font, fill=TEXT_COL)
        title_y += int(title_font.size * 1.2)

    if subtitle:
        sub_y = title_y + 10
        sub_lines = _wrap_text(draw, subtitle, subtitle_font, max_title_width)
        for line in sub_lines:
            draw.text((margin, sub_y), line, font=subtitle_font, fill=SUB)
            sub_y += int(subtitle_font.size * 1.3)
        title_y = sub_y + 10

    # ── Sections ──────────────────────────────────────────────────────────────
    sections = content.get("sections", [])
    section_font_h = _font(28, "semibold")
    section_font_b = _font(20, "regular")

    section_y = title_y + 30
    section_height = (height - section_y - 120) // max(len(sections), 1)
    section_height = min(section_height, 160)

    for i, section in enumerate(sections[:6]):
        heading = _ascii_safe(section.get("heading", ""))
        body = _ascii_safe(section.get("body", ""))

        # Section card background
        card_y = section_y + i * section_height
        card_rect = (margin, card_y, width - margin, card_y + section_height - 12)
        draw.rounded_rectangle(card_rect, radius=12, fill=(*CARD, 200))

        # Numbered badge (instead of emoji — WorkSans doesn't support emoji glyphs)
        badge_num = str(i + 1)
        badge_font = _font(24, "bold")
        badge_size = 42
        badge_x = margin + 20
        badge_y = card_y + 18
        draw.rounded_rectangle(
            (badge_x, badge_y, badge_x + badge_size, badge_y + badge_size),
            radius=10, fill=ACCENT,
        )
        # Center the number in the badge
        num_w = draw.textlength(badge_num, font=badge_font)
        draw.text(
            (badge_x + (badge_size - num_w) / 2, badge_y + 6),
            badge_num, font=badge_font, fill=BG,
        )

        # Heading
        heading_x = badge_x + badge_size + 15
        heading_y = badge_y + 4
        draw.text((heading_x, heading_y), heading, font=section_font_h, fill=TEXT_COL)

        # Body (wrapped)
        body_y = heading_y + int(section_font_h.size * 1.3)
        max_body_width = width - heading_x - margin - 20
        _draw_wrapped_text(
            draw, body, (heading_x, body_y),
            section_font_b, SUB, max_body_width,
            max_lines=3,
        )

    # ── Footer ────────────────────────────────────────────────────────────────
    footer = _ascii_safe(content.get("footer", ""))
    if footer:
        footer_font = _font(18, "regular")
        footer_y = height - 70
        _draw_wrapped_text(
            draw, footer, (margin, footer_y),
            footer_font, SUB, width - 2 * margin, max_lines=2,
        )

    # Export as PNG
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ── PIL text helpers ──────────────────────────────────────────────────────────

def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    """Wrap text to fit within max_width."""
    words = text.split()
    lines, cur = [], ""
    for word in words:
        test = (cur + " " + word).strip()
        if draw.textlength(test, font=font) <= max_width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines or [""]


def _draw_wrapped_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    xy: tuple[int, int],
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
    max_width: int,
    max_lines: int = 0,
) -> int:
    """Draw wrapped text. Returns the y position after the last line."""
    lines = _wrap_text(draw, text, font, max_width)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        # Add ellipsis to last line if truncated
        if lines:
            lines[-1] = lines[-1][:max(0, len(lines[-1]) - 3)] + "..."
    x, y = xy
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += int(font.size * 1.25)
    return y
