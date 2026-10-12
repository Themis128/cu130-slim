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
    twin_titles = [h for h in residual.hits if h.reason == "cross_slide_duplicate" and "title" in h.left_field]
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


def test_hit_and_report_to_dict():
    from app.services.duplicate_detector import DuplicateHit, DuplicateReport

    h = DuplicateHit(left_field="a", right_field="b", score=0.9, reason="r", left_snippet="x", right_snippet="y")
    d = h.to_dict()
    assert d["left_field"] == "a" and d["right_snippet"] == "y"

    r = DuplicateReport(has_duplicates=True, hits=[h], resolved=True, actions=["did"])
    rd = r.to_dict()
    assert rd["resolved"] is True and rd["hits"][0]["score"] == 0.9


def test_similarity_score_empty_and_is_duplicate_blank():
    assert similarity_score("   ", "text") == 0.0
    assert similarity_score("text", "") == 0.0
    assert is_duplicate("", "x") is False
    assert is_duplicate("x", "  ") is False


def test_detect_field_duplicates_prefix_and_pairs():
    from app.services.duplicate_detector import detect_field_duplicates

    hits = detect_field_duplicates(
        {"one": "same content here for testing", "two": "same content here for testing", "three": "totally different words"},
        prefix="slide.",
    )
    assert hits and hits[0].left_field == "slide.one"
    assert hits[0].right_field == "slide.two"


def test_detect_caption_slide_duplicates_paths():
    from app.services.duplicate_detector import detect_caption_slide_duplicates

    # empty caption → early return
    assert detect_caption_slide_duplicates([{"title": "x"}], "") == []
    # caption duplicating a slide field
    slides = [{"title": "No more server worries for you", "body": "unique body"}]
    hits = detect_caption_slide_duplicates(slides, "No more server worries for you")
    assert hits and hits[0].left_field == "caption"
    assert hits[0].right_field == "slide[0].title"


def test_pick_primary_empty():
    from app.services.duplicate_detector import _pick_primary

    assert _pick_primary("", "  ", None) == ""


def test_resolve_slide_promotes_body_and_highlight():
    # body-only slide → promoted to title
    slide, actions = resolve_slide_duplicates({"title": "", "body": "Only a body with enough words", "highlight": ""})
    assert slide["title"] == "Only a body with enough words"
    assert slide["body"] == ""
    assert "promoted_body_to_title" in actions

    # no title/body but a highlight → promoted highlight
    slide2, actions2 = resolve_slide_duplicates({"title": "", "body": "", "highlight": "Standout point"})
    assert slide2["title"] == "Standout point"
    assert slide2["highlight"] is None
    assert "promoted_highlight_to_title" in actions2


def test_resolve_cross_slide_duplicate_body_cleared():
    from app.services.duplicate_detector import resolve_cross_slide_duplicates

    # unique body → title rewritten instead of body cleared
    slides = [
        {"title": "Servers made simple for your business today", "body": ""},
        {"title": "Servers made simple for your business today", "body": "Completely unrelated body content here"},
    ]
    out, actions = resolve_cross_slide_duplicates(slides)
    assert any("rewrote_cross_slide_title" in a for a in actions)

    # right body ALSO duplicates the left value → body cleared outright
    slides = [
        {"title": "Servers made simple for your business today", "body": ""},
        {"title": "Servers made simple for your business today", "body": "Servers made simple for your business today"},
    ]
    out, actions = resolve_cross_slide_duplicates(slides)
    assert out[1]["body"] == ""
    assert any("cleared_cross_slide_duplicate_body" in a for a in actions)
