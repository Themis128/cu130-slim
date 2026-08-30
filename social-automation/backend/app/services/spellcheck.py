"""Automatic spell/grammar correction via LanguageTool.

Pre-processing order (per LanguageTool docs and NLP best practices):
  1. NFKC Unicode normalization  — LanguageTool requires this; collapses ligatures,
     curly quotes, fullwidth chars, etc. into canonical forms.
  2. Control-character strip      — remove invisible/zero-width chars that confuse offsets.
  3. Whitespace normalization     — collapse runs of spaces/tabs; strip leading/trailing.
  4. LanguageTool /v2/check      — offsets are now stable and correct.
  5. Apply corrections end→start  — no index drift.

For text going onto rendered images use `preprocess_for_render()` which additionally
strips emoji and other codepoints PIL fonts cannot display.
"""
import logging
import re
import unicodedata

import emoji
import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _strip_unrenderable_symbols(text: str) -> str:
    """Remove emoji and other high symbol codepoints that bundled PIL fonts cannot render.

    Uses the ``emoji`` package and the Unicode ``So`` category.  Keeping low
    codepoints (< U+2600) preserves common symbols such as currency, copyright
    and mathematical marks while stripping dingbats, arrows, geometric shapes,
    chess pieces and similar glyphs.
    """

    def _keep(ch: str) -> bool:
        if not ch:
            return False
        if emoji.is_emoji(ch):
            return False
        if unicodedata.category(ch) == "So" and ord(ch) >= 0x2600:
            return False
        return True

    return "".join(ch for ch in text if _keep(ch))


def _normalize(text: str) -> str:
    """Steps 1-3: NFKC + control-char strip + whitespace collapse."""
    # Step 1 — NFKC: LanguageTool's JLanguageTool.check() expects this.
    text = unicodedata.normalize("NFKC", text)
    # Step 2 — strip control/invisible characters (keep newlines for multiline text).
    text = "".join(ch for ch in text if unicodedata.category(ch) not in ("Cc", "Cf") or ch in "\n\r\t")
    # Step 3 — collapse whitespace runs; preserve deliberate newlines.
    text = re.sub(r"[ \t]+", " ", text).strip()
    return text


def preprocess_for_render(text: str) -> str:
    """Full normalization + emoji strip for text about to be drawn onto an image.

    PIL fonts (DejaVu, WorkSans) have no emoji glyphs — sending emoji produces
    tofu boxes or crashes. Strip them and collapse any resulting double spaces.
    """
    text = _normalize(text)
    text = _strip_unrenderable_symbols(text)
    text = re.sub(r" {2,}", " ", text).strip()
    return text


async def auto_correct(text: str, language: str = "en-US") -> str:
    """Return spell/grammar-corrected text after proper pre-processing.

    Falls back to the normalized original silently on any LanguageTool error.
    """
    if not text or not text.strip():
        return text

    # Pre-process before sending so LanguageTool offsets are stable.
    normalized = _normalize(text)

    settings = get_settings()
    lt_url = settings.LANGUAGETOOL_URL.rstrip("/")

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                f"{lt_url}/v2/check",
                data={"text": normalized, "language": language},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        resp.raise_for_status()
        matches = resp.json().get("matches", [])
    except Exception as exc:
        logger.warning("LanguageTool auto-correct unavailable: %s", exc)
        return normalized  # return at least the normalized form

    if not matches:
        return normalized

    # Apply replacements from end → start so earlier offsets stay valid.
    matches.sort(key=lambda m: m.get("offset", 0), reverse=True)
    corrected = normalized
    applied = 0
    for m in matches:
        replacements = m.get("replacements", [])
        if not replacements:
            continue
        offset = m.get("offset", 0)
        length = m.get("length", 0)
        best = replacements[0]["value"]
        corrected = corrected[:offset] + best + corrected[offset + length:]
        applied += 1

    if applied:
        logger.info("Auto-corrected %d issue(s) in %d-char text", applied, len(text))

    return corrected
