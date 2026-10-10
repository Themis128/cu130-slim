"""Unit tests for spellcheck preprocessing + integration tests against live LanguageTool."""

import pytest

import app.services.spellcheck as spellcheck
from app.services.spellcheck import (
    _normalize,
    auto_correct,
    detect_gibberish,
    preprocess_for_render,
)

# -- _normalize ----------------------------------------------------------------

def test_normalize_nfkc_fullwidth():
    # Fullwidth Latin (U+FF21...) -> ASCII under NFKC.
    text = "Ａｂｃ"   # A b c fullwidth
    result = _normalize(text)
    assert result == "Abc"


def test_normalize_nfkc_superscript():
    # Subscript 2 (U+2082) -> 2 under NFKC.
    result = _normalize("CO₂")
    assert "₂" not in result
    assert "2" in result


def test_normalize_nfkc_ligature():
    # fi ligature (U+FB01) -> fi under NFKC.
    result = _normalize("ﬁle")
    assert result == "file"


def test_normalize_collapses_whitespace():
    result = _normalize("hello   world\t!")
    assert result == "hello world !"


def test_normalize_strips_zero_width():
    # Zero-width joiner (U+200D) must be stripped -- corrupts LT offsets.
    text = "hel‍lo"
    result = _normalize(text)
    assert "‍" not in result
    assert "hello" in result


def test_normalize_strips_leading_trailing():
    assert _normalize("  hi  ") == "hi"


def test_normalize_preserves_newlines():
    result = _normalize("line one\nline two")
    assert "\n" in result


# -- preprocess_for_render -----------------------------------------------------

def test_preprocess_strips_emoji():
    result = preprocess_for_render("Ship fast \U0001f680 and iterate \U0001f504")
    assert "\U0001f680" not in result
    assert "\U0001f504" not in result
    assert "Ship fast" in result
    assert "and iterate" in result


def test_preprocess_no_double_spaces_after_emoji_strip():
    result = preprocess_for_render("hello \U0001f30d world")
    assert "  " not in result


def test_preprocess_plain_text_unchanged():
    text = "Clear skies. Zero friction."
    assert preprocess_for_render(text) == text


def test_preprocess_emoji_only_string():
    result = preprocess_for_render("\U0001f389\U0001f38a\U0001f388")
    assert result == ""


# -- auto_correct (integration -- requires live LanguageTool) ------------------


def _require_languagetool():
    """Skip auto_correct tests when the LanguageTool sidecar is unreachable —
    ``auto_correct`` swallows connection errors and returns input unchanged."""
    import httpx

    from app.core.config import get_settings
    lt_url = get_settings().LANGUAGETOOL_URL.rstrip("/")
    try:
        httpx.get(f"{lt_url}/v2/languages", timeout=5).raise_for_status()
    except Exception:
        pytest.skip(f"LanguageTool unavailable at {lt_url}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_auto_correct_fixes_spelling():
    _require_languagetool()
    result = await auto_correct("I havve a speling misteak here.")
    assert "havve" not in result


@pytest.mark.integration
@pytest.mark.asyncio
async def test_auto_correct_clean_text_unchanged():
    _require_languagetool()
    text = "We help small teams ship fast."
    result = await auto_correct(text)
    assert result == text


@pytest.mark.integration
@pytest.mark.asyncio
async def test_auto_correct_returns_normalized_form():
    _require_languagetool()
    # Fullwidth H -> ASCII H after NFKC normalization.
    text = "Ｈello world"
    result = await auto_correct(text)
    assert "Ｈ" not in result
    assert "ello world" in result


@pytest.mark.integration
@pytest.mark.asyncio
async def test_auto_correct_empty_string():
    _require_languagetool()
    assert await auto_correct("") == ""
    assert await auto_correct("   ") == "   "


@pytest.mark.integration
@pytest.mark.asyncio
async def test_auto_correct_offsets_stable_after_normalize():
    _require_languagetool()
    # Result must be valid text -- no truncation or garbling.
    text = "Ths is a tset sentance."
    result = await auto_correct(text)
    assert len(result) > 5
    assert result.endswith(".")


# -- protected vocabulary (Redis→Regis regression, post 3f58f3c1) ----------

def test_protected_spans_cover_configured_words():
    from app.services.spellcheck import _protected_spans

    text = "Just a Redis flag in the Kakkava playbook for cloudless teams."
    spans = _protected_spans(text)
    for word in ("Redis", "Kakkava", "cloudless"):
        start = text.index(word)
        assert any(s <= start < e for s, e in spans), f"{word} not protected"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_auto_correct_never_mangles_protected_words():
    _require_languagetool()
    # LanguageTool flags Redis→Regis; the protected-word list must win.
    text = "Added a Redis flag that remembers dead sessions."
    result = await auto_correct(text)
    assert "Redis" in result
    assert "Regis" not in result


# -- detect_gibberish ----------------------------------------------------------


class _FakeLTResponse:
    def __init__(self, matches):
        self._matches = matches

    def raise_for_status(self):
        pass

    def json(self):
        return {"matches": self._matches}


def _patch_languagetool(monkeypatch, flag_word=None, broken=False):
    """Deterministic LanguageTool for detect_gibberish tests.

    ``flag_word`` makes the fake LT report it as a ``misspelling`` match (the
    way real LT reports ``clientsget``); ``broken`` simulates the sidecar being
    down so only the local mixed-case signal can fire.
    """

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, data=None, headers=None):
            if broken:
                raise ConnectionError("LT down")
            text = (data or {}).get("text", "")
            matches = []
            if flag_word and flag_word in text:
                matches.append(
                    {
                        "offset": text.index(flag_word),
                        "length": len(flag_word),
                        "rule": {"issueType": "misspelling"},
                    }
                )
            return _FakeLTResponse(matches)

    monkeypatch.setattr(
        spellcheck.httpx, "AsyncClient", lambda *a, **k: _Client()
    )


@pytest.mark.asyncio
async def test_gibberish_historical_corruption(monkeypatch):
    """The exact LinkedIn incident: ``clientsget`` + ``cloudless.g GGr``."""
    _patch_languagetool(monkeypatch, flag_word="clientsget")
    text = (
        "Speed + security, handled edge-first We integrate Cloudflare "
        "across our stack so cloudless.g GGr clientsget: Global CDN"
    )
    flagged = await detect_gibberish(text)
    assert "GGr" in flagged
    assert "clientsget" in flagged


@pytest.mark.asyncio
async def test_gibberish_mixed_case_pattern(monkeypatch):
    _patch_languagetool(monkeypatch)
    flagged = await detect_gibberish("Ship the APIkey today")
    assert "APIkey" in flagged


@pytest.mark.asyncio
async def test_gibberish_plural_acronyms_exempt(monkeypatch):
    _patch_languagetool(monkeypatch)
    assert await detect_gibberish("Our APIs, PDFs and JWTs work") == []


@pytest.mark.asyncio
async def test_gibberish_camel_allowlist(monkeypatch):
    _patch_languagetool(monkeypatch)
    assert await detect_gibberish("Secure OAuth logins with QLoRA tuning") == []


@pytest.mark.asyncio
async def test_gibberish_protected_spans_untouched(monkeypatch):
    _patch_languagetool(monkeypatch)
    text = "Visit cloudless.gr or https://x.com/GGrAbc #GGrTag @GGhandle"
    assert await detect_gibberish(text) == []


@pytest.mark.asyncio
async def test_gibberish_clean_text(monkeypatch):
    _patch_languagetool(monkeypatch)
    assert await detect_gibberish("We help small teams ship faster.") == []


@pytest.mark.asyncio
async def test_gibberish_greek_not_flagged(monkeypatch):
    _patch_languagetool(monkeypatch, flag_word="Προσφέρουμε")
    # Non-ASCII scripts are never flagged even if LT reports them.
    assert await detect_gibberish("Προσφέρουμε αυτοματισμούς") == []


@pytest.mark.asyncio
async def test_gibberish_short_tokens_ignored(monkeypatch):
    _patch_languagetool(monkeypatch, flag_word="tehh")
    assert await detect_gibberish("a tehh short typo") == []


@pytest.mark.asyncio
async def test_gibberish_lt_down_still_catches_pattern(monkeypatch):
    _patch_languagetool(monkeypatch, broken=True)
    assert await detect_gibberish("see cloudless.g GGr here") == ["GGr"]


@pytest.mark.asyncio
async def test_gibberish_empty():
    assert await detect_gibberish("") == []
    assert await detect_gibberish("   ") == []
