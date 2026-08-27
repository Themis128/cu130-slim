"""Tests for duplicate content detector."""

from app.services.duplicate_detector import (
    detect_carousel_duplicates,
    is_duplicate,
    resolve_carousel_duplicates,
    resolve_slide_duplicates,
    similarity_score,
)


def test_server_worry_phrases_are_duplicates():
    a = "No more server worries for you"
    b = "Don't worry about servers. Our team manages them so you can focus on your business."
    assert similarity_score(a, b) >= 0.46
    assert is_duplicate(a, b)


def test_resolve_slide_clears_highlight_and_collapses():
    slide, actions = resolve_slide_duplicates(
        {
            "title": "No more server worries for you",
            "body": "We handle server management, updates, and security so you can work without worrying.",
            "highlight": "Don't worry about servers. Our team manages them so you can focus on your business.",
        }
    )
    assert slide["highlight"] is None
    assert actions
    # title/body about same worry/servers idea → single NLP line
    assert slide["title"]
    assert slide["body"] == "" or not is_duplicate(slide["title"], slide["body"])


def test_cross_slide_duplicate_cleared():
    slides = [
        {"title": "Ship without servers", "body": "Focus on product.", "highlight": None},
        {"title": "Ship without servers", "body": "Focus on product again.", "highlight": None},
    ]
    cleaned, caption, report = resolve_carousel_duplicates(slides, "Grow with cloudless.gr")
    assert report.resolved
    assert cleaned[0]["title"]
    # later twin should not keep an identical twin body unchecked
    residual = detect_carousel_duplicates(cleaned, caption)
    twin_titles = [
        h
        for h in residual.hits
        if h.reason == "cross_slide_duplicate" and "title" in h.left_field
    ]
    assert not twin_titles or cleaned[1]["title"] != cleaned[0]["title"]


def test_unrelated_fields_kept():
    slide, _ = resolve_slide_duplicates(
        {
            "title": "Ship faster",
            "body": "Focus on product while we handle the edge network for you.",
            "highlight": None,
        }
    )
    assert slide["title"] == "Ship faster"
    assert "edge" in slide["body"]
