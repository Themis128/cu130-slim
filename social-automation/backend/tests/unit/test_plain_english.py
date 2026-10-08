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
