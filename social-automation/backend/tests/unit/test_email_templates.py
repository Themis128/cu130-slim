"""Unit tests for email template functions."""

from app.services.email_templates import _html_wrapper


def test_html_wrapper_basic():
    """Test that _html_wrapper produces valid HTML with the title and body."""
    html = _html_wrapper("Test Title", "<p>Hello world</p>")
    assert "<!DOCTYPE html>" in html
    assert "<html" in html
    assert "Test Title" in html
    assert "<p>Hello world</p>" in html
    assert "SocialAuto" in html


def test_html_wrapper_contains_styles():
    """Test that the HTML wrapper includes CSS styles."""
    html = _html_wrapper("Title", "body")
    assert "<style>" in html
    assert ".container" in html
    assert ".btn" in html


def test_html_wrapper_escapes_none():
    """Test that the wrapper handles empty body."""
    html = _html_wrapper("Empty", "")
    assert "Empty" in html
    assert "<!DOCTYPE html>" in html
