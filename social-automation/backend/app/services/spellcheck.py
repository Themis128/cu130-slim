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


# Spans LanguageTool must never "correct": LinkedIn mention/hashtag markup,
# URLs, bare domains, and @/# handles. Proper nouns get mangled otherwise —
# "Kakkava" → "Baklava", "cloudless.gr" → "cloudless. Gr", and
# `@[Name](urn:li:person:X)` markup gets re-cased (`urn:LI:`) which breaks
# LinkedIn's mention parser.
_PROTECTED_PATTERNS = (
    re.compile(r"[@#]\[[^\]]*\]\([^)]*\)"),  # @[Name](urn:li:...) / #[tag](...)
    re.compile(r"https?://\S+|www\.\S+"),  # URLs
    re.compile(r"\b[\w-]+(?:\.[\w-]+)+\b"),  # bare domains / dotted tokens
    re.compile(r"[@#]\w+"),  # @handles and #hashtags
)


def _protected_word_pattern() -> re.Pattern[str] | None:
    """Whole-word alternation for configured vocabulary LanguageTool
    must not touch — proper nouns it otherwise "corrects" (Redis→Regis)."""
    words = [
        w.strip()
        for w in get_settings().LANGUAGETOOL_PROTECTED_WORDS.split(",")
        if w.strip()
    ]
    if not words:
        return None
    return re.compile(r"(?<![\w])(?:" + "|".join(re.escape(w) for w in words) + r")(?![\w])")


def _protected_spans(text: str) -> list[tuple[int, int]]:
    """Sorted, merged (start, end) ranges that LanguageTool must not touch."""
    spans: list[tuple[int, int]] = []
    for pat in _PROTECTED_PATTERNS:
        spans.extend((m.start(), m.end()) for m in pat.finditer(text))
    word_pat = _protected_word_pattern()
    if word_pat:
        spans.extend((m.start(), m.end()) for m in word_pat.finditer(text))
    spans.sort()
    merged: list[list[int]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]


def _mask(text: str, spans: list[tuple[int, int]]) -> str:
    """Replace protected spans with spaces, preserving length so LT offsets
    in the masked text stay valid for the original."""
    chars = list(text)
    for s, e in spans:
        for i in range(s, e):
            if chars[i] != "\n":
                chars[i] = " "
    return "".join(chars)


async def auto_correct(text: str, language: str = "en-US") -> str:
    """Return spell/grammar-corrected text after proper pre-processing.

    Falls back to the normalized original silently on any LanguageTool error.
    """
    if not text or not text.strip():
        return text

    # Pre-process before sending so LanguageTool offsets are stable.
    normalized = _normalize(text)
    protected = _protected_spans(normalized)
    masked = _mask(normalized, protected) if protected else normalized

    settings = get_settings()
    lt_url = settings.LANGUAGETOOL_URL.rstrip("/")

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                f"{lt_url}/v2/check",
                data={"text": masked, "language": language},
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
    # Masked text has identical length, so offsets map 1:1 onto `normalized`.
    matches.sort(key=lambda m: m.get("offset", 0), reverse=True)
    corrected = normalized
    applied = 0
    for m in matches:
        replacements = m.get("replacements", [])
        if not replacements:
            continue
        offset = m.get("offset", 0)
        length = m.get("length", 0)
        if any(offset < e and offset + length > s for s, e in protected):
            continue  # match touches a protected span — never apply
        best = replacements[0]["value"]
        corrected = corrected[:offset] + best + corrected[offset + length:]
        applied += 1

    if applied:
        logger.info("Auto-corrected %d issue(s) in %d-char text", applied, len(text))

    return corrected


# ── gibberish / mangle detection ──────────────────────────────────────────────
#
# LLM generation occasionally drops a separator inside a word, producing
# non-dictionary tokens (``clientsget``) or stray-capitalized fragments
# (``cloudless.g GGr`` — residue of a mangled ``.gr``). Both sailed through
# spellcheck + the NLP gate onto a live LinkedIn post, so this second-line
# detector flags them for the quality pipeline and the publish path.
#
# Two signals:
#   1. Mixed-case mangle — ``[A-Z]{2,}[a-z]`` inside a token (``GGr``,
#      ``APIkey``). Plural acronyms are exempt (``PDFs``, ``APIs`` — the
#      lowercase tail is exactly ``s``), as is a small allowlist of real
#      camel-case terms (``OAuth``, ``QLoRA``).
#   2. Residual misspellings — tokens LanguageTool still reports as
#      ``misspelling`` after auto_correct has run. Restricted to unprotected
#      ASCII-letter tokens ≥8 chars to keep the false-positive surface small;
#      non-ASCII scripts (e.g. Greek copy) are never flagged.
#
# Advisory like ``auto_correct``: a LanguageTool failure degrades to the
# pattern-only signal rather than raising.

_MANGLED_CASE = re.compile(r"\b[A-Za-z]*[A-Z]{2,}[a-z][A-Za-z]*\b")
_PLURAL_ACRONYM = re.compile(r"[A-Z]{2,}s")  # PDFs, APIs, JWTs — legit
_ASCII_WORD = re.compile(r"[A-Za-z]+")
_MISSPELLING_MIN_LEN = 8

# Real camel-case terms that match the mangle pattern.
_CAMEL_ALLOWLIST = frozenset({"oauth", "qlora", "dbaas", "onnx"})


def _mixed_case_mangles(text: str) -> list[str]:
    """Tokens with an embedded uppercase run followed by lowercase — the
    signature of a dropped separator (``.gr`` → ``GGr``, ``API key`` →
    ``APIkey``)."""
    flagged: list[str] = []
    for m in _MANGLED_CASE.finditer(text):
        token = m.group(0)
        if _PLURAL_ACRONYM.fullmatch(token):
            continue
        if token.lower() in _CAMEL_ALLOWLIST:
            continue
        flagged.append(token)
    return flagged


async def detect_gibberish(text: str, language: str = "en-US") -> list[str]:
    """Return suspicious non-dictionary / mangled tokens in ``text``.

    Runs after ``auto_correct`` so anything still flagged here is corruption
    LanguageTool could not fix. Never raises — LanguageTool unavailability
    falls back to the local mixed-case signal only.
    """
    if not text or not text.strip():
        return []

    normalized = _normalize(text)
    protected = _protected_spans(normalized)
    masked = _mask(normalized, protected) if protected else normalized

    flagged = _mixed_case_mangles(masked)

    try:
        settings = get_settings()
        lt_url = settings.LANGUAGETOOL_URL.rstrip("/")
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                f"{lt_url}/v2/check",
                data={"text": masked, "language": language},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        resp.raise_for_status()
        for m in resp.json().get("matches", []):
            if m.get("rule", {}).get("issueType") != "misspelling":
                continue
            token = normalized[m.get("offset", 0): m.get("offset", 0) + m.get("length", 0)]
            if len(token) >= _MISSPELLING_MIN_LEN and _ASCII_WORD.fullmatch(token):
                flagged.append(token)
    except Exception as exc:
        logger.warning("LanguageTool gibberish check unavailable: %s", exc)

    # Stable de-dup, capped — a handful of tokens is enough to diagnose.
    seen: dict[str, None] = {}
    for tok in flagged:
        seen.setdefault(tok, None)
    return list(seen)[:10]
