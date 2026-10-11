"""Unit tests for plain-English NLP checker / fixer."""

from unittest.mock import AsyncMock

import pytest

import app.services.plain_english as pe
from app.services.plain_english import (
    check_carousel_copy,
    check_plain_english,
    needs_plain_english_rewrite,
)


def test_detects_jargon():
    issues = check_plain_english("We leverage enterprise-grade synergy to unlock value.", "body")
    assert issues
    assert any(i.reason == "jargon_or_buzzwords" for i in issues)


def test_allows_plain_english():
    text = "We help small teams move to the cloud without long contracts."
    assert not needs_plain_english_rewrite(text)
    assert check_plain_english(text) == []


def test_carousel_check_flags_caption():
    report = check_carousel_copy(
        slides=[{"title": "Hello", "body": "Simple help for teams.", "highlight": None}],
        caption="Unlock transformative actionable insights with our robust platform.",
    )
    assert report.needs_fix
    assert any(i.field == "caption" for i in report.issues)


def test_extract_rewritten_only_drops_original_label():
    from app.services.plain_english import extract_rewritten_only

    raw = "Original: We leverage synergy.\n\nPlain English: We work well together."
    assert extract_rewritten_only(raw) == "We work well together."


def test_dedupe_slide_keeps_nlp_body_once():
    from app.services.plain_english import dedupe_slide_copy

    slide = dedupe_slide_copy(
        {
            "title": "No servers needed — our system runs in the cloud for you",
            "body": "No servers needed. Our system runs entirely in the cloud so you don't worry about maintenance.",
            "highlight": "No servers needed. Our system runs entirely in the cloud so you don't worry about maintenance.",
            "slide_type": "cover",
        }
    )
    assert slide["highlight"] is None
    assert slide["body"] == ""
    assert "servers" in slide["title"].lower()


def test_build_caption_no_duplicate_site():
    from app.services.plain_english import build_linkedin_caption

    text = build_linkedin_caption(
        "Ship faster with cloudless.gr\n\nwww.cloudless.gr",
        ["cloudless", "serverless"],
    )
    assert text.lower().count("www.cloudless.gr") == 1
    assert text.count("#cloudless") == 1


def test_sofia_rules_flag_ai_slop():
    from app.services.plain_english import check_sofia_rules

    text = (
        "In today's fast-paced digital landscape, it's no secret that teams need to move fast. "
        "Moreover, seamless tools unlock real outcomes — taking your workflow to the next level. "
        "At the end of the day, results matter most for everyone involved."
    )
    reasons = {i.reason for i in check_sofia_rules(text)}
    assert {"ai_tell_phrases", "weak_hook", "em_dash", "no_specifics", "missing_cta"} <= reasons


def test_sofia_rules_accept_clean_post():
    from app.services.plain_english import check_sofia_rules

    text = (
        "I finally shipped it.\n\n"
        "The checklist took 3 days. Clients kept asking for the same thing.\n\n"
        "So I wrote it down once. Now it sells while I sleep.\n\n"
        "Want a copy? Comment below.\n\n"
        "P.S. It is free, no signup."
    )
    assert check_sofia_rules(text) == []


def test_sofia_nlp_score_rewards_clean_punishes_slop():
    from app.services.plain_english import sofia_nlp_score

    good = "I shipped it in 3 days.\n\nClients asked for it every week.\n\nNow it sells while I sleep.\n\nWant one?"
    bad = (
        "In today's fast-paced digital landscape, it's no secret that enterprises leverage synergy. "
        "Moreover, seamless, cutting-edge, enterprise-grade holistic paradigms empower world-class "
        "digital transformation — unlocking scalable solutions that take your brand to the next level."
    )
    good_score, _ = sofia_nlp_score(good)
    bad_score, bad_issues = sofia_nlp_score(bad)
    assert good_score >= 80
    assert bad_score < 50
    assert any(i.reason == "ai_tell_phrases" for i in bad_issues)


def test_sofia_rules_require_ps_on_longform():
    from app.services.plain_english import check_sofia_rules

    long_no_ps = "I wrote a long post. " * 60
    reasons = {i.reason for i in check_sofia_rules(long_no_ps)}
    assert "missing_ps" in reasons

    long_with_ps = long_no_ps + "\n\nP.S. Steal this and adapt it."
    reasons = {i.reason for i in check_sofia_rules(long_with_ps)}
    assert "missing_ps" not in reasons


# ── coverage: report/report helpers ───────────────────────────────────


def test_report_to_dict_and_avg_words():
    from app.services.plain_english import NlpCheckReport, NlpIssue, _avg_sentence_words
    r = NlpCheckReport(needs_fix=True, issues=[NlpIssue("f", "r", "s")],
                       fixed=True, fields_rewritten=["x"], duplicates={"d": 1})
    d = r.to_dict()
    assert d["needs_fix"] and d["fixed"]
    assert d["issues"][0]["field"] == "f"
    assert _avg_sentence_words("") == 0.0
    assert _avg_sentence_words("One two. Three four five!") == 2.5


def test_check_plain_english_empty_and_long_sentences_and_words():
    assert check_plain_english("") == []
    assert check_plain_english("   ") == []

    long_sentence = "word " * 30 + "end."
    issues = check_plain_english(long_sentence, "body")
    assert any(i.reason == "long_sentences" for i in issues)

    long_words = "antidisestablishmentarianism pseudopseudohypoparathyroidism short."
    issues = check_plain_english(long_words)
    assert any(i.reason == "long_uncommon_words" for i in issues)


def test_any_needs_plain_english():
    assert not pe.any_needs_plain_english(["clean text", ""])
    assert not pe.any_needs_plain_english(["clean text", None])
    assert pe.any_needs_plain_english(["we leverage synergy"])


def test_extract_rewritten_only_paths():
    assert pe.extract_rewritten_only("") == ""
    # original marker at position 0 is stripped
    raw = "Original text: old stuff here."
    assert pe.extract_rewritten_only(raw) == "old stuff here."
    # duplicate consecutive sentences collapse
    dup = "Same sentence. Same sentence. Different."
    assert pe.extract_rewritten_only(dup).count("Same sentence") == 1


def test_texts_overlap(monkeypatch):
    import app.services.duplicate_detector as dd
    monkeypatch.setattr(dd, "is_duplicate", lambda a, b, threshold=0.5: True)
    assert pe.texts_overlap("a", "b") is True


def test_dedupe_carousel_copy(monkeypatch):
    import app.services.duplicate_detector as dd
    monkeypatch.setattr(
        dd, "resolve_carousel_duplicates",
        lambda slides, cap: (slides, cap, {"hits": []}))
    slides = [{"title": "t", "body": "b", "highlight": "h"},
              {"title": "t2", "body": "", "highlight": ""}]
    out_slides, out_cap, report = pe.dedupe_carousel_copy(slides, "cap")
    assert out_slides[0]["highlight"] == "h"
    assert report == {"hits": []}


def test_build_linkedin_caption_edge_tags():
    # empty tag skipped; site already present; caption empty
    out = pe.build_linkedin_caption("body www.cloudless.gr", ["", "x"])
    assert "#x" in out and out.count("www.cloudless.gr") == 1
    out2 = pe.build_linkedin_caption("", [])
    assert out2 == "www.cloudless.gr"


def test_check_carousel_highlight_field():
    report = pe.check_carousel_copy(
        [{"title": "ok", "body": "ok", "highlight": "leverage synergy"}], "")
    assert any(i.field.endswith(".highlight") for i in report.issues)


# ── rewrite / fix / pipeline ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_rewrite_plain_english_paths(monkeypatch):
    # clean text, no force -> passthrough
    assert await pe.rewrite_plain_english("simple clear text") == "simple clear text"
    assert await pe.rewrite_plain_english("") == ""

    import app.services.inference as inference
    monkeypatch.setattr(
        inference, "call_inference",
        AsyncMock(return_value={"text": "Rewritten: better copy"}))
    out = await pe.rewrite_plain_english(
        "we leverage synergy", force=True)
    assert "better copy" in out

    # empty model output -> original
    monkeypatch.setattr(inference, "call_inference",
                        AsyncMock(return_value={"text": ""}))
    assert await pe.rewrite_plain_english("x", force=True) == "x"

    # HTTPException -> original
    from fastapi import HTTPException
    monkeypatch.setattr(
        inference, "call_inference",
        AsyncMock(side_effect=HTTPException(status_code=500, detail="x")))
    assert await pe.rewrite_plain_english("x", force=True) == "x"

    # generic exception -> original
    monkeypatch.setattr(inference, "call_inference",
                        AsyncMock(side_effect=RuntimeError("x")))
    assert await pe.rewrite_plain_english("x", force=True) == "x"


@pytest.mark.asyncio
async def test_fix_carousel_copy(monkeypatch):
    async def _rewrite(text, **kw):
        return "better " + text
    monkeypatch.setattr(pe, "rewrite_plain_english", _rewrite)
    slides = [{"title": "t", "body": "b", "highlight": "h"},
              {"title": "", "body": "", "highlight": ""}]
    out_slides, cap, fields = await pe.fix_carousel_copy(
        slides=slides, caption="cap", force=True)
    assert out_slides[0]["title"] == "better t"
    assert "caption" in fields and "slide[0].title" in fields
    # second slide fields were empty -> skipped
    assert "slide[1].title" not in fields


@pytest.mark.asyncio
async def test_fix_carousel_copy_no_force_clean(monkeypatch):
    monkeypatch.setattr(pe, "rewrite_plain_english",
                        AsyncMock(side_effect=AssertionError("not called")))
    out_slides, cap, fields = await pe.fix_carousel_copy(
        slides=[{"title": "clean", "body": "clean"}], caption="clean")
    assert fields == [] and out_slides[0]["title"] == "clean"


@pytest.mark.asyncio
async def test_run_nlp_check_and_fix(monkeypatch):
    import app.services.duplicate_detector as dd
    monkeypatch.setattr(
        dd, "resolve_carousel_duplicates",
        lambda slides, cap: (
            slides, cap,
            type("R", (), {
                "to_dict": lambda s: {"d": 1},
                "hits": [type("H", (), {
                    "left_field": "a", "right_field": "b",
                    "reason": "exact", "left_snippet": "s",
                    "right_snippet": "r", "score": 0.9,
                    "to_dict": lambda s: {"h": 1},
                })()],
                "actions": ["act"],
            })(),
        ))
    monkeypatch.setattr(
        dd, "detect_carousel_duplicates",
        lambda slides, cap: type("R", (), {
            "hits": [type("H", (), {"to_dict": lambda s: {"r": 1}})()],
        })())

    async def _rewrite(text, **kw):
        return text + " fixed"
    monkeypatch.setattr(pe, "rewrite_plain_english", _rewrite)

    slides, cap, report = await pe.run_nlp_check_and_fix(
        slides=[{"title": "we leverage synergy", "body": "x"}],
        caption="cap",
        force_fix=True,
    )
    assert report.fixed is True
    assert report.fields_rewritten
    assert any(i.reason == "duplicate:exact" for i in report.issues)
    assert "residual_hits" in report.duplicates

    # should_fix False -> early return
    slides2, cap2, report2 = await pe.run_nlp_check_and_fix(
        slides=[{"title": "clean", "body": "clean"}],
        caption="clean", force_fix=False)
    assert report2.fixed is False


@pytest.mark.asyncio
async def test_run_nlp_residual_issues_after_fix(monkeypatch):
    import app.services.duplicate_detector as dd
    monkeypatch.setattr(dd, "resolve_carousel_duplicates",
                        lambda s, c: (s, c, type("R", (), {
                            "to_dict": lambda x: {}, "hits": [], "actions": []})()))
    monkeypatch.setattr(dd, "detect_carousel_duplicates",
                        lambda s, c: type("R", (), {"hits": []})())
    monkeypatch.setattr(pe, "rewrite_plain_english",
                        AsyncMock(side_effect=lambda t, **k: "still leverage synergy"))
    _, _, report = await pe.run_nlp_check_and_fix(
        slides=[{"title": "leverage", "body": ""}], caption="", force_fix=True)
    assert any(i.reason.startswith("residual_after_fix:") for i in report.issues)


@pytest.mark.asyncio
async def test_ensure_plain_english_carousel(monkeypatch):
    monkeypatch.setattr(
        pe, "run_nlp_check_and_fix",
        AsyncMock(return_value=([{"title": "x"}], "cap", "report")))
    slides, cap = await pe.ensure_plain_english_carousel(
        slides=[{}], caption="c")
    assert cap == "cap"


def test_sofia_rules_remaining_branches():
    from app.services.plain_english import check_sofia_rules
    # empty text
    assert check_sofia_rules("") == []

    # weak hook (non-starter opener, ≤12 words)
    reasons = {i.reason for i in check_sofia_rules(
        "Something happened at work last week.\n\nIt mattered 5 times. Ask me?")}
    assert "weak_hook" in reasons

    # generic hook opener
    reasons = {i.reason for i in check_sofia_rules(
        "Did you know this exists.\n\nIt did 3 times. Want it?")}
    assert "generic_hook" in reasons

    # flat rhythm: >=4 sentences all same band
    flat = ("Short one. Two here. Three now. Four too. Five last. "
            "It has 9 digits. Want it?")
    reasons = {i.reason for i in check_sofia_rules(flat)}
    assert "flat_rhythm" in reasons


@pytest.mark.asyncio
async def test_cloudflare_provider_model_defaults(monkeypatch):
    import app.services.duplicate_detector as dd
    import app.services.inference as inference

    monkeypatch.setattr(inference, "call_inference",
                        AsyncMock(return_value={"text": "ok"}))
    monkeypatch.setattr(dd, "resolve_carousel_duplicates",
                        lambda s, c: (s, c, type("R", (), {
                            "to_dict": lambda x: {}, "hits": [], "actions": []})()))
    monkeypatch.setattr(dd, "detect_carousel_duplicates",
                        lambda s, c: type("R", (), {"hits": []})())

    # each entry point's cloudflare model default
    await pe.rewrite_plain_english(
        "x", provider_name="cloudflare", force=True)
    await pe.fix_carousel_copy(
        slides=[{"title": "x"}], caption="",
        provider_name="cloudflare", force=True)
    await pe.run_nlp_check_and_fix(
        slides=[{"title": "x"}], caption="",
        provider_name="cloudflare", force_fix=True)
