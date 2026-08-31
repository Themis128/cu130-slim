"""Unit tests for SEO scoring, keyword extraction, and Open Graph generation."""

from app.services.seo import (
    extract_keywords,
    generate_open_graph,
    score_content,
)


# ── extract_keywords ──────────────────────────────────────────────────────────

def test_extract_keywords_returns_top_words():
    text = "Cloud migration cloud strategy cloud deployment automation tools"
    kws = extract_keywords(text, max_keywords=3)
    assert len(kws) <= 3
    # Each item is {"keyword": ..., "count": ...}
    keywords = [k["keyword"] for k in kws]
    assert "cloud" in keywords


def test_extract_keywords_strips_stopwords():
    text = "The best way to improve your SEO is to write good content"
    kws = extract_keywords(text, max_keywords=5)
    keywords = [k["keyword"] for k in kws]
    assert "the" not in keywords
    assert "is" not in keywords
    assert "to" not in keywords


def test_extract_keywords_empty_text():
    assert extract_keywords("") == []
    assert extract_keywords("   ") == []


def test_extract_keywords_min_length():
    text = "AI is the best tool for SEO and PR"
    kws = extract_keywords(text, max_keywords=10)
    # Words shorter than 3 chars should be excluded
    for k in kws:
        assert len(k["keyword"]) >= 3


# ── score_content ─────────────────────────────────────────────────────────────

def test_score_content_returns_dict_with_expected_keys():
    text = "Building a cloud-first strategy helps teams ship faster #cloud #devops https://example.com"
    report = score_content(text, "linkedin")
    d = report.to_dict()
    assert "overall" in d
    assert "readability" in d
    assert "keywords" in d
    assert "hashtags" in d
    assert "links" in d
    assert "plain_english" in d
    assert "length" in d
    assert "recommendations" in d


def test_score_content_higher_with_hashtags_and_links():
    good = "Building a cloud-first strategy helps teams ship faster #cloud #devops https://example.com"
    bare = "Building a cloud-first strategy helps teams ship faster"
    good_score = score_content(good, "linkedin").to_dict()["overall"]
    bare_score = score_content(bare, "linkedin").to_dict()["overall"]
    assert good_score > bare_score


def test_score_content_plain_english_penalty():
    jargony = "We leverage enterprise-grade synergy to unlock disruptive value."
    plain = "We help small teams move to the cloud without long contracts."
    jargon_score = score_content(jargony, "linkedin").to_dict()["plain_english"]
    plain_score = score_content(plain, "linkedin").to_dict()["plain_english"]
    assert plain_score > jargon_score


def test_score_content_platform_hints():
    text = "A short post"
    li_score = score_content(text, "linkedin").to_dict()["length"]
    tw_score = score_content(text, "twitter").to_dict()["length"]
    # Twitter has a lower max, so a short post scores better there
    assert tw_score >= li_score


def test_score_content_empty_text():
    report = score_content("", "linkedin")
    assert report.to_dict()["overall"] < 50


# ── generate_open_graph ───────────────────────────────────────────────────────

def test_generate_open_graph_with_meta():
    og = generate_open_graph("My Title", "Body text here", "My description")
    assert og["og_title"] == "My Title"
    assert og["og_description"] == "My description"
    assert og["og_type"] == "article"
    assert og["twitter_card"] == "summary_large_image"
    assert og["twitter_title"] == "My Title"


def test_generate_open_graph_falls_back_to_body():
    body = "This is a longer body of text that exceeds sixty characters easily"
    og = generate_open_graph("", body)
    # og_title should be the first 60 chars of body, stripped
    assert og["og_title"] == body[:60].strip()
    assert len(og["og_description"]) > 0


def test_generate_open_graph_empty_inputs():
    og = generate_open_graph("", "", "")
    assert og["og_title"] == ""
    assert og["og_type"] == "article"
