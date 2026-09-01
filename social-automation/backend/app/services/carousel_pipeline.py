"""End-to-end Cloudflare carousel pipeline for n8n / automation."""

from __future__ import annotations

import base64
import io
import os
import random
import re
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.content import Post, PostStatus, PostTarget
from app.models.social_account import SocialAccount
from app.models.user import Team, User
from app.services.cf_models import CF_TEXT_FREE, CF_TXT2IMG_FREE
from app.services.duplicate_detector import is_duplicate
from app.services.inference import (
    _call_workers_ai_image,
    call_inference,
)
from app.services.media_storage import persist_generated_image
from app.services.plain_english import (
    PLAIN_ENGLISH_RULES,
    build_linkedin_caption,
    run_nlp_check_and_fix,
)
from app.services.spellcheck import auto_correct, preprocess_for_render

DEFAULT_ORG_ACCOUNT_ID = os.environ.get(
    "CLOUDLESS_LINKEDIN_ORG_ACCOUNT_ID",
    "4a8d9440-47d2-4bda-bd11-3776fd9022ba",
)

# ── Brand tokens ──────────────────────────────────────────────────────────────
BG      = (15,  15,  23)   # #0f0f17
CARD    = (22,  22,  34)   # slightly lighter panel
ACCENT  = (0,   255, 245)  # #00fff5  exact cloudless cyan
ACCENT2 = (255, 100,  40)  # warm orange accent
TEXT    = (225, 235, 245)  # near-white body copy
SUB     = (120, 135, 160)  # muted blue-grey
GRID    = (30,  30,  45)   # subtle grid line colour

_FONT_DIR = "/app/app/assets/fonts"
_FONT_FILES = {
    "bold":     os.path.join(_FONT_DIR, "WorkSans-Bold.ttf"),
    "semibold": os.path.join(_FONT_DIR, "WorkSans-SemiBold.ttf"),
    "regular":  os.path.join(_FONT_DIR, "WorkSans-Regular.ttf"),
}
_FALLBACK = {
    "bold":    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "regular": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
}


def _font(size: int, weight: str = "regular"):
    path = _FONT_FILES.get(weight, _FONT_FILES["regular"])
    if os.path.exists(path):
        return ImageFont.truetype(path, size)
    fb = _FALLBACK.get("bold" if weight == "bold" else "regular", _FALLBACK["regular"])
    if os.path.exists(fb):
        return ImageFont.truetype(fb, size)
    return ImageFont.load_default()


def _draw_wrapped(draw, text, xy, font_obj, fill, max_width):
    words = text.split()
    lines, cur = [], ""
    for word in words:
        test = (cur + " " + word).strip()
        if draw.textlength(test, font=font_obj) <= max_width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    x, y = xy
    for line in lines:
        draw.text((x, y), line, font=font_obj, fill=fill)
        y += int(font_obj.size * 1.25)
    return y


def _ascii_safe(text: str) -> str:
    """Replace fancy dashes/quotes so DejaVu never shows tofu glyphs."""
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


def _draw_grid(draw: ImageDraw.ImageDraw) -> None:
    """Subtle dot-grid overlay for depth."""
    step = 54
    for gx in range(0, 1080, step):
        for gy in range(0, 1080, step):
            draw.ellipse((gx - 1, gy - 1, gx + 1, gy + 1), fill=GRID)


def _draw_header(draw: ImageDraw.ImageDraw, index: int, total: int) -> None:
    # Gradient accent bar — thicker for visibility (14px)
    for x in range(1080):
        t = x / 1079
        color = (
            int(ACCENT[0] * (1 - t) + ACCENT2[0] * t),
            int(ACCENT[1] * (1 - t) + ACCENT2[1] * t),
            int(ACCENT[2] * (1 - t) + ACCENT2[2] * t),
        )
        draw.line([(x, 0), (x, 13)], fill=color)
    # Logo — bold and clear, larger for brand visibility
    brand_font = _font(40, "bold")
    dot_font = _font(40, "regular")
    cw = draw.textlength("cloudless", font=brand_font)
    draw.text((80, 44), "cloudless", font=brand_font, fill=ACCENT)
    draw.text((80 + cw, 44), ".gr", font=dot_font, fill=SUB)
    # Slide counter — subtle pill badge (right side)
    counter = f"{index:02d} / {total:02d}"
    cf = _font(22)
    clen = int(draw.textlength(counter, font=cf))
    px, py = 980 - clen, 52
    draw.rounded_rectangle((px - 12, py - 4, px + clen + 12, py + 30), radius=14, fill=(30, 32, 48))
    draw.text((px, py), counter, font=cf, fill=SUB)


def _draw_soft_orbs(draw: ImageDraw.ImageDraw) -> None:
    """Subtle glow orbs — brand atmosphere, no letters."""
    for cx, cy, r, fill in (
        (980, 180, 160, (0,  42,  40)),   # top-right cyan glow
        (100, 960, 200, (20,  20,  48)),   # bottom-left indigo
        (990, 920, 110, (50,  20,  10)),   # bottom-right orange
        (170, 200,  80, (0,   38,  36)),   # top-left cyan hint
    ):
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fill)


def _motif_for(slide_type: str, index: int) -> str:
    if slide_type == "cover":
        return "cover"
    if slide_type == "cta":
        return "cta"
    if slide_type == "stat":
        return "stat"
    motifs = ("servers", "pricing", "calendar", "rocket", "chat", "compare")
    # After a cover (01), content 02 -> servers, 03 -> pricing, ...
    return motifs[max(0, index - 2) % len(motifs)]


def _draw_infographic(
    draw: ImageDraw.ImageDraw,
    *,
    motif: str,
    highlight: str | None,
    chart_data: dict | None = None,
) -> None:
    """Data-driven infographics — charts, tables, process flows (no AI text)."""
    # Background card with semi-transparent dark overlay for readability
    draw.rounded_rectangle((80, 530, 1000, 930), radius=32, fill=CARD, outline=(35, 55, 75), width=2)
    RED = (220, 70, 70)

    if motif == "cover":
        # Comparison table: Traditional vs Cloudless
        draw.text((110, 550), "Traditional Cloud", font=_font(24, "bold"), fill=RED)
        draw.text((590, 550), "cloudless.gr", font=_font(24, "bold"), fill=ACCENT)
        # Divider
        draw.line([(540, 545), (540, 920)], fill=(40, 50, 68), width=2)
        rows = [
            ("Servers to manage", "Fully managed"),
            ("Long contracts", "Monthly billing"),
            ("Surprise invoices", "Clear pricing"),
            ("Ops team needed", "Ship-ready"),
        ]
        for i, (bad, good) in enumerate(rows):
            y = 610 + i * 72
            draw.rounded_rectangle((100, y, 520, y + 52), radius=10, fill=(30, 22, 28))
            draw.text((116, y + 14), bad, font=_font(22), fill=(200, 180, 180))
            draw.rounded_rectangle((560, y, 980, y + 52), radius=10, fill=(10, 36, 42))
            draw.text((576, y + 14), good, font=_font(22), fill=TEXT)
        return

    if motif == "servers":
        # Horizontal bar chart: old-way vs cloudless ops cost
        draw.text((110, 555), "Monthly ops effort (hours)", font=_font(24, "bold"), fill=SUB)
        bars = [
            ("Self-hosted", 0.90, RED),
            ("VPS managed", 0.62, (220, 140, 50)),
            ("cloudless.gr", 0.15, ACCENT),
        ]
        for i, (label, frac, color) in enumerate(bars):
            y = 628 + i * 90
            draw.text((110, y), label, font=_font(22), fill=SUB)
            bx1, bx2 = 110, 930
            bw = int(bx1 + (bx2 - bx1) * frac)
            draw.rounded_rectangle((bx1, y + 32, bx2, y + 62), radius=10, fill=(30, 36, 48))
            draw.rounded_rectangle((bx1, y + 32, bw, y + 62), radius=10, fill=color)
            pct = f"{int(frac * 100)}%"
            draw.text((bw + 10, y + 36), pct, font=_font(22, "bold"), fill=color)
        draw.rounded_rectangle((110, 900, 520, 920), radius=6, fill=(0, 60, 55))
        draw.text((118, 900), "85% reduction with cloudless.gr", font=_font(18, "semibold"), fill=ACCENT)
        return

    if motif == "pricing":
        # Cost breakdown: 3 clear rows with icon placeholders
        draw.text((110, 555), "What you actually pay", font=_font(24, "bold"), fill=SUB)
        items = [
            ("Setup fee",   "$0",      ACCENT),
            ("Monthly run", "from $29", TEXT),
            ("Support",     "included", ACCENT),
            ("Exit penalty","$0",       ACCENT),
        ]
        for i, (label, price, color) in enumerate(items):
            y = 618 + i * 72
            draw.rounded_rectangle((100, y, 700, y + 54), radius=12, fill=(26, 30, 46))
            draw.text((120, y + 14), label, font=_font(24), fill=SUB)
            pw = int(draw.textlength(price, font=_font(26, "bold")))
            draw.text((700 - pw, y + 12), price, font=_font(26, "bold"), fill=color)
        draw.rounded_rectangle((720, 618, 980, 914), radius=16, fill=(10, 38, 46), outline=ACCENT, width=2)
        draw.text((760, 680), "No", font=_font(52, "bold"), fill=ACCENT)
        draw.text((750, 748), "hidden", font=_font(32, "bold"), fill=ACCENT)
        draw.text((760, 800), "fees.", font=_font(32, "bold"), fill=ACCENT)
        return

    if motif == "calendar":
        # Deployment timeline — numbered milestones
        draw.text((110, 555), "Go live in days, not months", font=_font(24, "bold"), fill=SUB)
        steps = [
            ("Day 1", "Sign up & configure"),
            ("Day 2", "Connect your repo"),
            ("Day 3", "First deploy live"),
            ("Week 2", "Production traffic"),
        ]
        for i, (when, what) in enumerate(steps):
            y = 630 + i * 72
            # Number circle
            cx = 148
            draw.ellipse((cx - 26, y - 4, cx + 26, y + 44), fill=ACCENT if i < 3 else ACCENT2)
            num = str(i + 1)
            nw = int(draw.textlength(num, font=_font(26, "bold")))
            draw.text((cx - nw // 2, y + 6), num, font=_font(26, "bold"), fill=BG)
            # Connector line
            if i < len(steps) - 1:
                draw.line([(cx, y + 44), (cx, y + 72)], fill=(40, 50, 65), width=3)
            draw.text((192, y + 2), when, font=_font(22, "bold"), fill=TEXT)
            draw.text((192, y + 28), what, font=_font(20), fill=SUB)
        return

    if motif == "rocket":
        draw.text((110, 555), "By the numbers", font=_font(24, "bold"), fill=SUB)
        if chart_data and isinstance(chart_data.get("items"), list) and len(chart_data["items"]) >= 2:
            kpis = [(str(item.get("value", "")), str(item.get("label", ""))) for item in chart_data["items"][:3]]
        else:
            kpis = [
                ("< 5 min", "deploy time"),
                ("99.9%",   "uptime SLA"),
                ("80%",     "ops saved"),
            ]
        for i, (val, label) in enumerate(kpis):
            x = 110 + i * 296
            draw.rounded_rectangle((x, 610, x + 266, 820), radius=20, fill=(20, 28, 44), outline=(40, 55, 75), width=2)
            vw = int(draw.textlength(val, font=_font(42, "bold")))
            draw.text((x + (266 - vw) // 2, 652), val, font=_font(42, "bold"), fill=ACCENT)
            lw = int(draw.textlength(label, font=_font(22)))
            draw.text((x + (266 - lw) // 2, 710), label, font=_font(22), fill=SUB)
        tip = _ascii_safe(highlight) if highlight else "Real results, real teams."
        tw = int(draw.textlength(tip[:44], font=_font(22, "semibold")))
        draw.text(((1080 - tw) // 2, 848), tip[:44], font=_font(22, "semibold"), fill=TEXT)
        return

    if motif == "chat":
        # Testimonial-style quote card
        draw.text((110, 555), "What teams say", font=_font(24, "bold"), fill=SUB)
        draw.rounded_rectangle((100, 608, 960, 828), radius=20, fill=(20, 28, 44), outline=(40, 55, 75), width=2)
        # Large quote mark
        draw.text((120, 612), "“", font=_font(80, "bold"), fill=ACCENT)
        quote = highlight or "We cut our infra time by 80% and ship twice as fast."
        quote = _ascii_safe(quote)
        _draw_wrapped(draw, f'"{quote}"', (130, 660), _font(26), TEXT, 800)
        draw.text((130, 840), "- cloudless.gr customer", font=_font(22, "semibold"), fill=SUB)
        return

    if motif == "stat":
        big = _ascii_safe(highlight) if highlight else "80%"
        bw = int(draw.textlength(big, font=_font(110, "bold")))
        draw.text(((1080 - bw) // 2, 590), big, font=_font(110, "bold"), fill=ACCENT)
        label = (chart_data or {}).get("label", "") if chart_data else ""
        if not label:
            label = "key metric"
        label = _ascii_safe(label)
        lw = int(draw.textlength(label[:50], font=_font(26)))
        draw.text(((1080 - lw) // 2, 720), label[:50], font=_font(26), fill=SUB)
        # Gauge bar
        gauge_x1, gauge_x2 = 160, 920
        draw.rounded_rectangle((gauge_x1, 778, gauge_x2, 814), radius=18, fill=(30, 38, 54))
        try:
            frac = min(1.0, float(big.strip("%")) / 100)
        except ValueError:
            frac = 0.5
        filled = int(gauge_x1 + (gauge_x2 - gauge_x1) * frac)
        draw.rounded_rectangle((gauge_x1, 778, filled, 814), radius=18, fill=ACCENT)
        draw.text((gauge_x1, 830), "Before cloudless.gr", font=_font(20), fill=(100, 100, 120))
        draw.text((gauge_x2 - 180, 830), "After", font=_font(20), fill=ACCENT)
        return

    if motif == "cta":
        # CTA centred with pill button
        draw.rounded_rectangle((150, 600, 930, 800), radius=36, fill=ACCENT)
        cta_text = "Start free at cloudless.gr"
        cta_w = int(draw.textlength(cta_text, font=_font(38, "bold")))
        draw.text(((1080 - cta_w) // 2, 666), cta_text, font=_font(38, "bold"), fill=BG)
        sub = "Quick setup. No contracts. Cancel anytime."
        sw = int(draw.textlength(sub, font=_font(26)))
        draw.text(((1080 - sw) // 2, 826), sub, font=_font(26), fill=SUB)
        return

    # Default: feature comparison two-column
    draw.rounded_rectangle((100, 570, 510, 900), radius=20, fill=(30, 22, 28), outline=RED, width=2)
    draw.text((170, 608), "Complex", font=_font(34, "bold"), fill=RED)
    rows_l = ("Manual scaling", "Long contracts", "Big ops team", "Unpredictable cost")
    for i, r in enumerate(rows_l):
        draw.text((120, 668 + i * 54), f"- {r}", font=_font(22), fill=(180, 150, 150))
    draw.rounded_rectangle((570, 570, 980, 900), radius=20, fill=(10, 36, 42), outline=ACCENT, width=2)
    draw.text((660, 608), "Simple", font=_font(34, "bold"), fill=ACCENT)
    rows_r = ("Auto-scales", "Open standards", "Ship in days", "Clear pricing")
    for i, r in enumerate(rows_r):
        draw.text((590, 668 + i * 54), f"+ {r}", font=_font(22), fill=TEXT)


def compose_branded_slide(
    bg_img: Image.Image | None,
    *,
    index: int,
    total: int,
    slide_type: str,
    title: str,
    body: str,
    highlight: str | None = None,
    motif: str | None = None,
    chart_data: dict | None = None,
) -> Image.Image:
    """Infographic slide: vector art + PIL text only (correct spelling, no AI glyphs)."""
    title = _ascii_safe(title)
    body = _ascii_safe(body)
    highlight = _ascii_safe(highlight) if highlight else None
    if body and title and is_duplicate(title, body):
        if len(body) >= len(title):
            title, body = body, ""
        else:
            body = ""

    # Brand canvas — dark base, blended with realistic CF-generated background.
    img = Image.new("RGB", (1080, 1080), BG)
    if bg_img is not None:
        bg_resized = bg_img.resize((1080, 1080), Image.Resampling.LANCZOS).convert("RGB")
        img = Image.blend(img, bg_resized, alpha=0.35)
    draw = ImageDraw.Draw(img)
    _draw_grid(draw)
    _draw_soft_orbs(draw)
    _draw_header(draw, index, total)

    stype = (slide_type or "content").lower()
    motif_key = (motif or _motif_for(stype, index)).lower()

    # ── Copy zone ─────────────────────────────────────────────────────────────
    PAD = 80
    INFOGRAPHIC_TOP = 530  # y where the infographic card begins
    FOOTER_TOP = 1044      # y where the footer strip begins
    MAX_TEXT_BOTTOM = INFOGRAPHIC_TOP - 16  # stop text before infographic

    if stype == "cover":
        # Pill tagline just below header
        tag = "Clear skies. Zero friction."
        tw = int(draw.textlength(tag, font=_font(22, "semibold"))) + 36
        draw.rounded_rectangle((PAD, 122, PAD + tw, 162), radius=20, outline=ACCENT, width=2)
        draw.text((PAD + 18, 131), tag, font=_font(22, "semibold"), fill=ACCENT)

    y = 196 if stype == "cover" else 152
    if title:
        # Auto-shrink title font for long titles to avoid overflow
        title_len = len(title)
        if title_len > 60:
            title_size = 38
        elif title_len > 44:
            title_size = 44
        elif title_len > 30:
            title_size = 52
        else:
            title_size = 62
        y = _draw_wrapped(draw, title, (PAD, y), _font(title_size, "bold"), TEXT, 940)
        y += 18

    if body and y < MAX_TEXT_BOTTOM:
        # Auto-shrink body font if remaining space is tight
        remaining = MAX_TEXT_BOTTOM - y
        body_font_size = 32 if remaining > 120 else 26 if remaining > 60 else 22
        # Truncate body to fit available space
        max_chars = max(60, int(remaining / 1.4 * (940 / 26)))
        if len(body) > max_chars:
            body = body[:max_chars].rsplit(" ", 1)[0] + "..."
        _draw_wrapped(draw, body, (PAD, y), _font(body_font_size, "regular"), SUB, 940)

    _draw_infographic(draw, motif=motif_key, highlight=highlight, chart_data=chart_data)

    # Footer brand strip
    draw.rectangle((0, 1044, 1080, 1080), fill=(10, 12, 20))
    draw.line([(0, 1044), (1080, 1044)], fill=ACCENT, width=1)
    draw.text((PAD, 1052), "cloudless.gr", font=_font(22, "bold"), fill=ACCENT)
    tagline = "Clear skies. Zero friction."
    tw = int(draw.textlength(tagline, font=_font(20)))
    draw.text((1080 - PAD - tw, 1054), tagline, font=_font(20), fill=SUB)
    return img

def _dedupe_slide_copy(slides: list[dict]) -> list[dict]:
    """Drop near-duplicate titles/bodies across slides; keep first unique idea."""
    seen: list[str] = []
    out: list[dict] = []
    for slide in slides:
        title = (slide.get("title") or "").strip()
        body = (slide.get("body") or "").strip()
        if any(is_duplicate(title, prev) or (body and is_duplicate(body, prev)) for prev in seen):
            continue
        if body and title and is_duplicate(title, body):
            body = ""
        seen.append(title)
        if body:
            seen.append(body)
        out.append({**slide, "title": title, "body": body})
    return out or slides


_PHOTO_STYLES = [
    "shot on Canon EOS R5, 35mm lens, natural light",
    "shot on Sony A7IV, wide angle, golden hour",
    "drone photography, DJI Mavic, bird's eye view",
    "Hasselblad medium format, shallow depth of field",
    "shot on Fujifilm X-T5, moody color grading",
    "DSLR macro photography, bokeh background",
    "editorial photography, studio lighting setup",
    "National Geographic style, vivid colors",
    "architectural photography, leading lines",
    "street photography, urban environment, contrast",
    "low angle shot, dramatic perspective",
    "long exposure photography, motion blur",
    "tilt-shift miniature effect, selective focus",
    "infrared photography style, surreal tones",
]

_MOODS = [
    "warm tones, amber and gold",
    "cool tones, blue and teal",
    "high contrast, dramatic shadows",
    "soft diffused light, pastel undertones",
    "neon-lit, cyberpunk atmosphere",
    "minimalist, clean composition",
    "moody, dark and atmospheric",
    "bright and airy, overexposed highlights",
]


async def _cf_generate_background(
    image_prompt: str,
    txt2img_model: str,
) -> Image.Image | None:
    """Generate a unique realistic background via Cloudflare Workers AI only.

    Adds random photographic style and mood modifiers to ensure every generation
    looks different, even for similar prompts. If CF is unavailable, returns None
    and the slide is rendered on the brand canvas.
    """
    style = random.choice(_PHOTO_STYLES)
    mood = random.choice(_MOODS)
    prompt_t2i = (
        f"{image_prompt}, {style}, {mood}, "
        f"professional photography, high quality, sharp focus, "
        f"no text, no letters, no words, no watermark"
    )
    raw_bytes = None

    try:
        t2i_result = await _call_workers_ai_image(
            prompt_t2i,
            model=txt2img_model,
            steps=4,
        )
        raw_bytes = base64.b64decode(t2i_result["image_base64"])
    except Exception as e:
        print(f"[carousel] CF txt2img failed (non-fatal): {e}", flush=True)

    if raw_bytes is None:
        return None

    try:
        return Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    except Exception as e:
        print(f"[carousel] Could not decode image: {e}", flush=True)
        return None


async def generate_carousel_copy(
    *,
    topic: str,
    num_slides: int,
    tone: str,
    include_cta: bool,
    text_model: str,
    text_provider: str = "cloudflare",
    db: AsyncSession,
    team_id,
) -> dict:
    num = max(3, min(10, num_slides))
    prompt = f"""Create a {num}-slide LinkedIn infographic carousel for cloudless.gr about: "{topic}"

CAPTION RULES (most important):
- 2-3 short sentences max. No marketing clichés.
- Open with a human observation or a surprising fact, not "We are excited..."
- End with one clear takeaway or gentle question to drive comments.
- Do NOT mention "no credit card" — cloudless.gr accepts credit cards.
- Tone: {tone}, conversational, like a smart friend sharing a tip.

SLIDE RULES:
- Each slide = one clear idea, plain English. Max 12 words per title.
- body: max 90 chars, adds one concrete detail (not a restatement of the title).
- highlight: a number, stat, or very short phrase to emphasise visually (or null).
- slide_type: cover (first), content (middle), stat (if there's a number), cta (last if include_cta={include_cta}).
- UNIQUENESS: no two slides share the same benefit/idea. No filler words.

IMAGE PROMPT RULES (one per slide — CRITICAL for visual quality):
- image_prompt: a short (15-25 words) realistic/photographic scene description for the slide background.
- Style: professional photography, real-world scenes, no abstract patterns, no text/letters/words in image.
- Each slide MUST have a COMPLETELY DIFFERENT scene, setting, and subject. Vary the environment
  (indoor/outdoor/aerial/macro), time of day, and visual subject.
- Be SPECIFIC and CREATIVE — describe exact objects, materials, environments. Generic prompts like
  "technology background" or "business meeting" are NOT allowed.
- Good examples:
  "close-up of fiber optic cables with blue light pulses inside dark server room",
  "hands assembling a Raspberry Pi IoT sensor on a wooden workbench",
  "raindrops on a glass window reflecting blurred city traffic at night",
  "overhead shot of a whiteboard covered in architecture diagrams and sticky notes".
- BAD examples (too generic): "technology background", "business concept", "abstract network", "person working".
- Never reuse scenes between slides. Each must tell a different visual story.

INFOGRAPHIC DATA (for slides with numbers/comparisons):
- chart_data: optional object with data for infographic rendering.
  For stat slides: {{"value": "85%", "label": "reduction in deploy time", "comparison": "was 2 hours, now 18 min"}}
  For comparison slides: {{"items": [{{"label": "Before", "value": "4 hours"}}, {{"label": "After", "value": "18 min"}}]}}
  For list slides: {{"items": ["point 1", "point 2", "point 3"]}}
  Leave null for cover/cta slides.

{PLAIN_ENGLISH_RULES}

Return JSON only:
- slides: array of exactly {num} objects with title, body, highlight, slide_type, image_prompt, chart_data
- suggested_caption: the engaging 2-3 sentence post caption
- hashtags: 5-7 relevant hashtags without #"""

    schema = {
        "type": "object",
        "properties": {
            "slides": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "body": {"type": "string"},
                        "highlight": {"type": ["string", "null"]},
                        "slide_type": {"type": "string"},
                        "image_prompt": {"type": "string"},
                        "chart_data": {"type": ["object", "null"]},
                    },
                    "required": ["title", "body", "slide_type", "image_prompt"],
                },
            },
            "suggested_caption": {"type": "string"},
            "hashtags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["slides", "suggested_caption", "hashtags"],
    }
    return await call_inference(
        prompt,
        provider_name=text_provider,
        db=db,
        team_id=team_id,
        schema=schema,
        model_override=text_model,
        allow_fallback=False,
    )


async def run_cloudless_carousel_pipeline(
    *,
    db: AsyncSession,
    user: User,
    team: Team,
    topic: str,
    num_slides: int = 7,
    tone: str = "clear and friendly",
    include_cta: bool = True,
    text_model: str = CF_TEXT_FREE,
    text_provider: str = "cloudflare",   # CF primary; Ollama is last resort
    txt2img_model: str = CF_TXT2IMG_FREE,
    target_account_id: str | None = None,
    publish: bool = True,
    wait_for_publish: bool = False,
    custom_slides: list[dict] | None = None,
    custom_caption: str | None = None,
    custom_hashtags: list[str] | None = None,
) -> dict:
    """Full pipeline: CF copy → NLP → txt2img bg → brand slides → post → publish.

    Text and images use Cloudflare Workers AI only (no Ollama/ComfyUI fallbacks).
    If CF image generation fails for a slide, the slide is rendered on the brand canvas.
    If custom_slides is provided, AI copy generation is skipped and the provided
    slides are used directly (still goes through NLP fix and brand composition).
    """
    target_id = uuid.UUID(target_account_id or DEFAULT_ORG_ACCOUNT_ID)
    account = (
        await db.execute(
            select(SocialAccount).where(
                SocialAccount.id == target_id,
                SocialAccount.team_id == team.id,
                SocialAccount.status == "active",
            )
        )
    ).scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=400, detail=f"LinkedIn target account not found: {target_id}")

    # 1) Copy — use custom slides if provided, otherwise Cloudflare Workers AI
    effective_provider = "cloudflare"
    if custom_slides:
        slides = list(custom_slides)[:num_slides]
        caption = custom_caption or "cloudless.gr — Clear skies. Zero friction."
        hashtags = custom_hashtags or ["cloudless", "serverless", "cloud"]
        # Minimal NLP report for custom slides
        from app.services.plain_english import NlpCheckReport
        nlp_report = NlpCheckReport(needs_fix=False, fixed=False, fields_rewritten=[], issues=[], duplicates={})
    else:
        raw = await generate_carousel_copy(
            topic=topic,
            num_slides=num_slides,
            tone=tone,
            include_cta=include_cta,
            text_model=text_model,
            text_provider=effective_provider,
            db=db,
            team_id=team.id,
        )

        slides = list(raw.get("slides") or [])[:num_slides]
        caption = raw.get("suggested_caption") or "We help small teams ship fast. cloudless.gr"
        hashtags = raw.get("hashtags") or ["cloudless", "serverless", "cloud"]

        # 2) NLP checker + fixer (runs on both slides and caption)
        slides, caption, nlp_report = await run_nlp_check_and_fix(
            slides=slides,
            caption=caption,
            provider_name=effective_provider,
            model=text_model,
            db=db,
            team_id=team.id,
            force_fix=True,
            allow_fallback=False,
        )
        slides = _dedupe_slide_copy(slides)
    # Skip dedupe for custom slides — they are intentionally curated

    # 3) Per-slide CF txt2img: unique realistic background for each slide
    media_ids: list[uuid.UUID] = []
    for i, slide in enumerate(slides):
        title = slide.get("title") or f"Slide {i + 1}"
        body = slide.get("body") or ""
        highlight = slide.get("highlight") or None
        stype = slide.get("slide_type") or "content"
        image_prompt = slide.get("image_prompt") or f"professional photo related to {title}"

        # Pre-process then spell-correct before text is burned into image pixels.
        title = preprocess_for_render(await auto_correct(title))
        body = preprocess_for_render(await auto_correct(body))
        if highlight:
            highlight = preprocess_for_render(await auto_correct(highlight))

        print(f"[n8n-pipeline] slide {i + 1}/{len(slides)} — generating background: {image_prompt[:60]}…", flush=True)
        bg_img = await _cf_generate_background(image_prompt, txt2img_model)
        if bg_img:
            print(f"[n8n-pipeline] slide {i + 1} background ready", flush=True)
        else:
            print(f"[n8n-pipeline] slide {i + 1} CF unavailable — pure brand canvas", flush=True)

        branded = compose_branded_slide(
            bg_img,
            index=i + 1,
            total=len(slides),
            slide_type=stype,
            title=title,
            body=body,
            highlight=highlight,
            motif=slide.get("visual") or slide.get("motif"),
            chart_data=slide.get("chart_data"),
        )
        buf = io.BytesIO()
        branded.save(buf, format="PNG", optimize=True)
        safe_topic = re.sub(r"[^a-zA-Z0-9_-]", "-", topic)[:32]
        folder_name = f"carousel-{safe_topic}"
        asset = await persist_generated_image(
            db,
            team_id=team.id,
            user_id=user.id,
            image_bytes=buf.getvalue(),
            prompt=f"n8n-carousel:{title}",
            source="n8n-cf-pipe",
            folder=folder_name,
        )
        media_ids.append(asset.id)

    full_caption = build_linkedin_caption(caption, hashtags)

    post = Post(
        team_id=team.id,
        user_id=user.id,
        status=PostStatus.DRAFT,
        content_text=full_caption,
        media_ids=media_ids,
        hashtags=hashtags,
        link_url="https://www.cloudless.gr",
        meta_data={
            "carousel": {
                "pipeline": "n8n-cf-txt2img-nlp",
                "txt2img_model": txt2img_model,
                "text_model": text_model,
                "nlp": nlp_report.to_dict(),
                "slides": slides,
                "account": account.display_name,
            }
        },
    )
    db.add(post)
    await db.flush()
    db.add(PostTarget(post_id=post.id, social_account_id=account.id, status="pending"))
    await db.commit()
    await db.refresh(post)

    result = {
        "post_id": str(post.id),
        "media_ids": [str(m) for m in media_ids],
        "slides": slides,
        "caption": caption,
        "hashtags": hashtags,
        "nlp_report": nlp_report.to_dict(),
        "target_account": {
            "id": str(account.id),
            "display_name": account.display_name,
            "account_type": (account.meta_data or {}).get("account_type"),
        },
        "status": post.status.value,
        "platform_url": None,
    }

    if not publish:
        return result

    # Mark scheduled so Celery beat can publish even if .delay() flakes.
    from app.worker.celery_app import celery_app
    from app.worker.tasks.publishing import process_publish_queue, publish_post_now

    post.status = PostStatus.SCHEDULED
    post.scheduled_at = datetime.now(UTC)
    await db.commit()
    result["status"] = post.status.value

    queue_error: str | None = None
    try:
        with celery_app.connection_or_acquire() as conn:
            publish_post_now.apply_async(
                args=[str(post.id), [str(account.id)]],
                connection=conn,
            )
            process_publish_queue.apply_async(connection=conn, countdown=2)
    except Exception as exc:  # noqa: BLE001 — still return post; beat may pick it up
        queue_error = f"{type(exc).__name__}: {exc}"
        print(f"[n8n-pipeline] celery queue failed (beat may still publish): {queue_error}", flush=True)
        result["queue_warning"] = queue_error

    if not wait_for_publish:
        return result

    # Optional brief poll (disabled by default for n8n so the webhook returns 200 quickly)
    import asyncio

    for _ in range(40):
        await asyncio.sleep(3)
        refreshed = (
            await db.execute(
                select(Post)
                .options(selectinload(Post.targets))
                .where(Post.id == post.id)
            )
        ).scalar_one()
        await db.refresh(refreshed)
        for t in refreshed.targets:
            await db.refresh(t)
        result["status"] = refreshed.status.value
        result["failure_reason"] = refreshed.failure_reason
        urls = [t.platform_url for t in refreshed.targets if t.platform_url]
        if urls:
            result["platform_url"] = urls[0]
        if refreshed.status in (PostStatus.PUBLISHED, PostStatus.FAILED):
            break

    return result
