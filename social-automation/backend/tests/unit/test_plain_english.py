"""Unit tests for plain-English NLP checker / fixer."""

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
