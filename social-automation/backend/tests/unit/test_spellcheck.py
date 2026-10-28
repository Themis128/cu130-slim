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


# -- remaining gaps: mask/word-pattern internals + mocked auto_correct -------


def test_strip_unrenderable_so_categories():
    from app.services.spellcheck import _strip_unrenderable_symbols

    # < U+2600 currency symbols kept; ≥ U+2600 So and emoji stripped
    assert _strip_unrenderable_symbols("price €10") == "price €10"
    assert _strip_unrenderable_symbols("go ➜ there ★") == "go  there "


def test_protected_word_pattern_none_when_unset(monkeypatch):
    from app.services.spellcheck import _protected_word_pattern

    class _S:
        LANGUAGETOOL_PROTECTED_WORDS = " , ,"

    monkeypatch.setattr(spellcheck, "get_settings", lambda: _S())
    assert _protected_word_pattern() is None


def test_mask_preserves_newlines_and_length():
    from app.services.spellcheck import _mask

    text = "line1\nhttps://x.com/a\nline3"
    spans = [(6, 21)]  # the URL
    masked = _mask(text, spans)
    assert len(masked) == len(text)
    assert masked[6:21] == " " * 15
    assert masked[5] == "\n" and masked[21] == "\n"
    assert masked[:5] == "line1"


class _LTClient:
    """Deterministic LanguageTool stub returning canned matches."""

    def __init__(self, resp, fail=False):
        self._resp = resp
        self._fail = fail

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, data=None, headers=None):
        if self._fail:
            raise ConnectionError("down")
        return self._resp


class _LTResp:
    def __init__(self, matches):
        self._matches = matches

    def raise_for_status(self):
        pass

    def json(self):
        return {"matches": self._matches}


@pytest.mark.asyncio
async def test_auto_correct_applies_match_end_to_start(monkeypatch):
    # One misspelling at a known offset → replacement applied.
    text = "I havve a dog."
    idx = text.index("havve")
    resp = _LTResp([{"offset": idx, "length": 5,
                     "replacements": [{"value": "have"}]}])
    monkeypatch.setattr(spellcheck.httpx, "AsyncClient",
                        lambda *a, **k: _LTClient(resp))
    out = await spellcheck.auto_correct(text)
    assert "have" in out and "havve" not in out


@pytest.mark.asyncio
async def test_auto_correct_skips_match_on_protected_span(monkeypatch):
    # Match inside a URL must never apply — protection wins.
    text = "see https://ex.com/tehh"
    idx = text.index("tehh")
    resp = _LTResp([{"offset": idx, "length": 4,
                     "replacements": [{"value": "the"}]}])
    monkeypatch.setattr(spellcheck.httpx, "AsyncClient",
                        lambda *a, **k: _LTClient(resp))
    assert await spellcheck.auto_correct(text) == text


@pytest.mark.asyncio
async def test_auto_correct_no_replacements_and_lt_down(monkeypatch):
    # Match without replacements → skipped, text unchanged.
    text = "hello worlld"
    resp = _LTResp([{"offset": 6, "length": 6, "replacements": []}])
    monkeypatch.setattr(spellcheck.httpx, "AsyncClient",
                        lambda *a, **k: _LTClient(resp))
    assert await spellcheck.auto_correct(text) == text

    # LT down → normalized text returned, no raise.
    monkeypatch.setattr(spellcheck.httpx, "AsyncClient",
                        lambda *a, **k: _LTClient(None, fail=True))
    assert await spellcheck.auto_correct("hi  there") == "hi there"


@pytest.mark.asyncio
async def test_auto_correct_empty_and_blank():
    assert await spellcheck.auto_correct("") == ""
    assert await spellcheck.auto_correct("   ") == "   "


# -- auto_correct_detailed: category filter + overlap + report ----------------
#
# LanguageTool's HTTP surface is the only stubbed boundary — the masking,
# filtering, and offset machinery all runs for real.


class _FakeLTResp:
    def __init__(self, matches):
        self._matches = matches

    def raise_for_status(self):
        return None

    def json(self):
        return {"matches": self._matches}


class _FakeLTClient:
    def __init__(self, matches):
        self._matches = matches

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, data=None, headers=None):
        return _FakeLTResp(self._matches)


def _lt_match(offset, length, repl, rule_id="TEST_RULE", category="TYPOS"):
    return {
        "offset": offset,
        "length": length,
        "message": "test message",
        "rule": {"id": rule_id, "category": {"id": category}},
        "replacements": [{"value": repl}],
    }


def _patch_lt(monkeypatch, matches):
    client = _FakeLTClient(matches)
    monkeypatch.setattr(spellcheck.httpx, "AsyncClient", lambda **_kw: client)


@pytest.mark.asyncio
async def test_detailed_applies_error_rule_and_reports(monkeypatch):
    from app.services.spellcheck import auto_correct_detailed

    _patch_lt(monkeypatch, [_lt_match(2, 5, "have")])
    corrected, applied = await auto_correct_detailed("I havve cats.")
    assert corrected == "I have cats."
    assert len(applied) == 1
    assert applied[0]["rule_id"] == "TEST_RULE"
    assert applied[0]["category"] == "TYPOS"
    assert applied[0]["replacement"] == "have"


@pytest.mark.asyncio
async def test_detailed_skips_style_category(monkeypatch):
    from app.services.spellcheck import auto_correct_detailed

    _patch_lt(monkeypatch, [_lt_match(0, 10, "Ship code", category="STYLE")])
    corrected, applied = await auto_correct_detailed("Ship code.")
    assert corrected == "Ship code."
    assert applied == []


@pytest.mark.asyncio
async def test_detailed_skips_ai_rule_prefix(monkeypatch):
    from app.services.spellcheck import auto_correct_detailed

    _patch_lt(
        monkeypatch,
        [_lt_match(0, 4, "This", rule_id="AI_HB_GEC_FIX", category="MISC")],
    )
    corrected, applied = await auto_correct_detailed("This are ok.")
    assert applied == []
    assert corrected == "This are ok."


@pytest.mark.asyncio
async def test_detailed_overlap_first_processed_wins(monkeypatch):
    """Two rules covering the same span — the higher-offset match is applied
    (walked first, end→start) and the overlapping one is dropped."""
    from app.services.spellcheck import auto_correct_detailed

    # "I havve cats." — match A covers 'avve' (offset 3..7), match B covers
    # 'havve' (offset 2..7). A processed first (higher offset), B skipped.
    _patch_lt(
        monkeypatch,
        [
            _lt_match(2, 5, "have", rule_id="RULE_B"),
            _lt_match(3, 4, "ave", rule_id="RULE_A"),
        ],
    )
    corrected, applied = await auto_correct_detailed("I havve cats.")
    assert corrected == "I have cats."
    assert [a["rule_id"] for a in applied] == ["RULE_A"]


@pytest.mark.asyncio
async def test_detailed_never_touches_protected_spans(monkeypatch):
    from app.services.spellcheck import auto_correct_detailed

    text = "Deploy to cloudless.gr today."
    start = text.index("cloudless.gr")
    _patch_lt(monkeypatch, [_lt_match(start, 12, "cloudless. Gr")])
    corrected, applied = await auto_correct_detailed(text)
    assert "cloudless.gr" in corrected
    assert applied == []


@pytest.mark.asyncio
async def test_detailed_lt_down_returns_normalized(monkeypatch):
    from app.services.spellcheck import auto_correct_detailed

    class _DownClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            raise RuntimeError("LT unreachable")

    monkeypatch.setattr(spellcheck.httpx, "AsyncClient", lambda **_kw: _DownClient())
    corrected, applied = await auto_correct_detailed("  hello   world ")
    assert corrected == "hello world"  # normalization still applies
    assert applied == []


@pytest.mark.asyncio
async def test_detailed_no_matches_returns_normalized(monkeypatch):
    from app.services.spellcheck import auto_correct_detailed

    _patch_lt(monkeypatch, [])
    corrected, applied = await auto_correct_detailed("All   good  here.")
    assert corrected == "All good here."
    assert applied == []


@pytest.mark.asyncio
async def test_auto_correct_wrapper_returns_only_text(monkeypatch):
    _patch_lt(monkeypatch, [_lt_match(0, 4, "This")])
    result = await auto_correct("Thsi works.")
    assert isinstance(result, str)
    assert result.startswith("This")
