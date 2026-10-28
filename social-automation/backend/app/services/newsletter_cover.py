"""Newsletter cover renderer — The Cloudless Build Log.

Renders the weekly-issue banner (1200x675 PNG) in the Cloudless brand style:
dark navy gradient, cyan masthead, large wrapped title, issue meta line.
Pure PIL — deterministic text (AI image models cannot spell), so every issue
gets pixel-perfect typography. The optional FLUX background layer used by the
infographic pipeline is intentionally not needed here: the gradient + glow
orbs already carry the brand look.
"""

from __future__ import annotations

import io
from datetime import date

from PIL import ImageDraw

from app.services.infographic_renderer import (
    _ascii_safe,
    _create_gradient_background,
    _font,
    _wrap_text,
)

WIDTH = 1200
HEIGHT = 675
CYAN = (0, 255, 245)
WHITE = (238, 242, 247)
MUTED = (148, 158, 172)
MARGIN = 64


def render_newsletter_cover(
    title: str,
    *,
    issue_number: int | None = None,
    issue_date: str | None = None,
    category: str | None = None,
) -> bytes:
    """Render the issue cover and return PNG bytes.

    title is wrapped to at most 3 lines; the meta line carries the optional
    issue number, date and category. Never raises on long titles — extra
    lines are dropped.
    """
    title = _ascii_safe(title).strip() or "Untitled issue"
    img = _create_gradient_background(WIDTH, HEIGHT)
    draw = ImageDraw.Draw(img)

    draw.text((MARGIN, 44), "THE CLOUDLESS BUILD LOG", font=_font(30, "bold"), fill=CYAN)
    draw.text((MARGIN, 90), "N E W S L E T T E R", font=_font(15), fill=MUTED)

    title_font = _font(66, "bold")
    lines = _wrap_text(draw, title, title_font, WIDTH - 2 * MARGIN)[:3]
    y = 190
    for line in lines:
        draw.text((MARGIN, y), line, font=title_font, fill=WHITE)
        y += 84

    meta_parts = []
    if issue_number:
        meta_parts.append(f"Issue #{issue_number}")
    if issue_date:
        meta_parts.append(str(issue_date))
    if category:
        meta_parts.append(str(category))
    if meta_parts:
        draw.text(
            (MARGIN, HEIGHT - 132),
            "  ·  ".join(meta_parts),
            font=_font(26),
            fill=MUTED,
        )

    draw.text((MARGIN, HEIGHT - 72), "cloudless.gr", font=_font(24, "bold"), fill=CYAN)

    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def default_issue_date() -> str:
    return date.today().isoformat()
