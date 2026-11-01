"""Image text guard — keep diffusion models from printing garbled text.

AI image models (FLUX schnell, Wan) cannot spell. Any prompt clause that
asks for a caption, banner, quote or lettering comes back as nonsense
baked into the pixels (observed live 2026-10-28: a monetization-report
image rendered "*5 company's success successs: the story.it we
exppertals rrecust ..."). The text-side quality pipeline (spellcheck,
NLP, SEO) validates prompt/caption/alt_text fields but never what the
model paints — this module closes that gap:

1. :func:`strip_text_render_requests` — deterministically removes
   caption/banner/text clauses from generation prompts.
2. :func:`apply_no_text_guard` — strips + appends strong anti-text
   directives the diffusion models actually respect.
3. :func:`wants_rendered_text` — detects explicit "render this text"
   requests (quoted strings) so callers can route to the PIL overlay
   (infographic) path, where text is always spelled correctly.
4. :func:`check_image_text` — QA on the generated pixels: DMR vision
   (qwen3-vl) transcribes all visible text, then :func:`detect_gibberish`
   judges the transcription. Garbled → callers retry with a sanitized
   prompt or fall back to PIL-rendered text.

Everything here is non-raising: a guard failure must never break media
generation, only leave it as-is (the pre-guard behavior).
"""

from __future__ import annotations

import base64
import logging
import re

logger = logging.getLogger(__name__)

# Segments (comma/semicolon/period-separated clauses) containing any of
# these are text-render requests and get dropped from generation prompts.
# Word-boundary matched, case-insensitive. Kept precise — ambiguous scene
# verbs ("reading books", "saying goodbye") must survive; the pixel-level
# QA (check_image_text) catches whatever the strip misses.
_TEXT_RENDER_KEYWORDS = (
    "caption",
    "captions",
    "subtitl",  # subtitle/subtitles/subtitling
    "lettering",
    "typography",
    "text overlay",
    "overlay text",
    "that says",
    "with the text",
    "with text",
    "text that",
    "banner",
    "quote",
    "headline",
    "wordmark",
    "watermark text",
)

# A segment also counts as a text request when it mentions plain "text"
# (word boundary — "context" does not match) with a render verb nearby.
_BARE_TEXT_RE = re.compile(r"\btext\b", re.IGNORECASE)

_SEGMENT_SPLIT_RE = re.compile(r"(?<=[,;])\s+")

_ANTITEXT_SUFFIX = (
    "absolutely NO TEXT, NO WORDS, NO LETTERS, NO WRITING, NO TYPOGRAPHY, NO CAPTIONS, NO LABELS, NO NUMBERS, NO SIGNS, NO WATERMARKS, clean scene, no overlays"
)

# Explicit "render this exact text" requests: quoted strings or "text: ..."
_QUOTED_TEXT_RE = re.compile(r"[\"'“”‘’]([^\"'“”‘’]{3,120})[\"'“”‘’]")
_TEXT_FIELD_RE = re.compile(r"\btext\s*:\s*\S", re.IGNORECASE)

_TRANSCRIBE_PROMPT = (
    "Transcribe ALL visible text, words or letters in this image exactly "
    "as written, including banners, signs and overlays. If there is no "
    "text at all, reply with exactly NONE."
)

_NONE_REPLIES = {"none", "none.", "no text", "no text.", "n/a"}


def _is_text_render_segment(segment: str) -> bool:
    lowered = segment.lower()
    if any(kw in lowered for kw in _TEXT_RENDER_KEYWORDS):
        return True
    return bool(_BARE_TEXT_RE.search(lowered))


def strip_text_render_requests(prompt: str) -> str:
    """Drop comma/semicolon-separated clauses that ask the model to render
    text (captions, banners, quotes, lettering). Scene clauses survive."""
    if not (prompt or "").strip():
        return prompt
    segments = _SEGMENT_SPLIT_RE.split(prompt.strip())
    kept = [s for s in segments if not _is_text_render_segment(s)]
    if not kept:  # every clause was a text request — keep the first, sanitized
        return ""
    return ", ".join(s.strip(" ,;") for s in kept)


def apply_no_text_guard(prompt: str) -> str:
    """Strip text-render clauses and append anti-text directives.

    Idempotent: prompts already carrying the guard suffix pass through.
    Empty results (all clauses were text requests) degrade to the suffix
    alone so the model still gets a valid instruction.
    """
    if not (prompt or "").strip():
        return prompt
    if "NO TEXT" in prompt:
        return prompt  # already guarded (idempotent)
    stripped = strip_text_render_requests(prompt)
    if not stripped:
        return _ANTITEXT_SUFFIX
    return f"{stripped}, {_ANTITEXT_SUFFIX}"


def wants_rendered_text(prompt: str) -> bool:
    """True when the prompt explicitly asks for exact text to appear
    (quoted strings or ``text: ...``) — route those to the PIL overlay
    path instead of trusting the model to spell."""
    if not (prompt or "").strip():
        return False
    return bool(_QUOTED_TEXT_RE.search(prompt) or _TEXT_FIELD_RE.search(prompt))


async def check_image_text(image_bytes: bytes) -> dict:
    """QA the generated pixels for garbled text.

    Transcribes visible text via DMR vision (qwen3-vl, local) and runs
    :func:`detect_gibberish` on the transcription. Returns::

        {"checked": bool, "has_text": bool, "transcription": str,
         "garbled": bool, "tokens": [str], "reason": str}

    Never raises — ``checked: False`` means QA infrastructure was
    unavailable and the caller must treat the image as pass-through.
    """
    result: dict = {
        "checked": False,
        "has_text": False,
        "transcription": "",
        "garbled": False,
        "tokens": [],
        "reason": "",
    }
    if not image_bytes:
        return result
    try:
        from app.services.media_ai import _call_dmr_vision

        b64 = base64.b64encode(image_bytes).decode()
        transcription = (await _call_dmr_vision(b64, _TRANSCRIBE_PROMPT, max_tokens=200)) or ""
    except Exception as exc:  # noqa: BLE001
        result["reason"] = f"vision unavailable: {type(exc).__name__}"
        return result

    transcription = transcription.strip()
    if not transcription or transcription.lower() in _NONE_REPLIES:
        result["checked"] = True
        result["reason"] = "no visible text"
        return result

    result["checked"] = True
    result["has_text"] = True
    result["transcription"] = transcription[:600]
    try:
        from app.services.spellcheck import detect_gibberish

        tokens = await detect_gibberish(transcription)
    except Exception as exc:  # noqa: BLE001
        result["reason"] = f"gibberish check failed: {type(exc).__name__}"
        return result

    result["tokens"] = tokens[:20]
    result["garbled"] = bool(tokens)
    result["reason"] = "garbled in-image text" if tokens else "text looks clean"
    if tokens:
        logger.warning("[image-text-guard] garbled text in generated image: %s", tokens[:8])
    return result
