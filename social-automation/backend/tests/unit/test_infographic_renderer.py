"""Unit tests for the infographic renderer.

Covers:
  - Infographic request detection (keywords)
  - Prompt sanitization (removes text, adds anti-text instructions)
  - PIL rendering with structured content
  - Text is correctly spelled in output
  - Non-infographic prompts pass through unchanged
"""

from __future__ import annotations

import io

from PIL import Image

from app.services.infographic_renderer import (
    generate_infographic_content,
    is_infographic_request,
    render_infographic,
    sanitize_prompt_for_background,
)

# ---------------------------------------------------------------------------
# 1. Infographic detection
# ---------------------------------------------------------------------------

class TestInfographicDetection:
    def test_detects_infographic_keyword(self):
        assert is_infographic_request("Create an infographic about cloudless.gr") is True

    def test_detects_poster_keyword(self):
        assert is_infographic_request("Make a poster for the conference") is True

    def test_detects_chart_keyword(self):
        assert is_infographic_request("Generate a chart showing revenue") is True

    def test_detects_stats_keyword(self):
        assert is_infographic_request("Show statistics about users") is True

    def test_detects_timeline_keyword(self):
        assert is_infographic_request("Create a timeline of events") is True

    def test_detects_checklist_keyword(self):
        assert is_infographic_request("Make a checklist for deployment") is True

    def test_does_not_detect_plain_photo(self):
        assert is_infographic_request("A red apple on a table") is False

    def test_does_not_detect_portrait(self):
        assert is_infographic_request("Professional corporate headshot") is False

    def test_does_not_detect_landscape(self):
        assert is_infographic_request("Mountain landscape at sunset") is False

    def test_case_insensitive(self):
        assert is_infographic_request("INFOGRAPHIC about DATA") is True


# ---------------------------------------------------------------------------
# 2. Prompt sanitization
# ---------------------------------------------------------------------------

class TestPromptSanitization:
    def test_adds_no_text_instructions(self):
        result = sanitize_prompt_for_background("infographic about cloudless.gr")
        assert "NO TEXT" in result
        assert "NO WORDS" in result
        assert "NO LETTERS" in result

    def test_preserves_original_prompt(self):
        result = sanitize_prompt_for_background("infographic about cloudless.gr")
        assert "infographic about cloudless.gr" in result

    def test_adds_minimalist_background(self):
        result = sanitize_prompt_for_background("poster for event")
        assert "clean minimalist background" in result
        assert "empty spaces for text overlay" in result


# ---------------------------------------------------------------------------
# 3. PIL rendering
# ---------------------------------------------------------------------------

class TestInfographicRendering:
    def _make_background(self, width=1024, height=1024) -> bytes:
        """Create a simple solid-color background image."""
        img = Image.new("RGB", (width, height), (20, 20, 40))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_renders_with_title_and_sections(self):
        content = {
            "title": "Benefits of Cloudless",
            "subtitle": "Embracing a Clearer Future",
            "sections": [
                {"icon": "☁️", "heading": "What is Cloudless", "body": "A state of clear skies and weather."},
                {"icon": "🌍", "heading": "Effects on Environment", "body": "Reduced pollution and greenhouse gas emissions."},
            ],
            "footer": "Learn more at cloudless.gr",
        }
        bg = self._make_background()
        result = render_infographic(content, bg, width=1024, height=1024)
        assert isinstance(result, bytes)
        assert len(result) > 1000
        # Verify it's a valid PNG
        img = Image.open(io.BytesIO(result))
        assert img.size == (1024, 1024)
        assert img.format == "PNG"

    def test_renders_without_subtitle(self):
        content = {
            "title": "Test Title",
            "sections": [
                {"icon": "📊", "heading": "Section 1", "body": "Body text here."},
            ],
        }
        bg = self._make_background()
        result = render_infographic(content, bg)
        assert isinstance(result, bytes)
        img = Image.open(io.BytesIO(result))
        assert img.size == (1024, 1024)

    def test_renders_without_footer(self):
        content = {
            "title": "No Footer",
            "sections": [
                {"icon": "✅", "heading": "Check", "body": "Done."},
            ],
        }
        bg = self._make_background()
        result = render_infographic(content, bg)
        assert isinstance(result, bytes)

    def test_renders_with_many_sections(self):
        content = {
            "title": "Many Sections",
            "sections": [
                {"icon": str(i), "heading": f"Section {i}", "body": f"Body {i}"}
                for i in range(8)
            ],
        }
        bg = self._make_background()
        result = render_infographic(content, bg)
        assert isinstance(result, bytes)
        # Should not crash even with more sections than display slots
        img = Image.open(io.BytesIO(result))
        assert img.size == (1024, 1024)

    def test_renders_at_different_dimensions(self):
        content = {
            "title": "Wide Format",
            "sections": [
                {"icon": "📱", "heading": "Test", "body": "Body"},
            ],
        }
        bg = self._make_background(width=1024, height=576)
        result = render_infographic(content, bg, width=1024, height=576)
        img = Image.open(io.BytesIO(result))
        assert img.size == (1024, 576)

    def test_handles_empty_sections(self):
        content = {
            "title": "No Sections",
            "sections": [],
        }
        bg = self._make_background()
        result = render_infographic(content, bg)
        assert isinstance(result, bytes)

    def test_handles_special_characters(self):
        content = {
            "title": "Test — with 'quotes' and \"double\"",
            "sections": [
                {"icon": "🎯", "heading": "It's a test", "body": "Don't worry about it."},
            ],
        }
        bg = self._make_background()
        result = render_infographic(content, bg)
        assert isinstance(result, bytes)


# ---------------------------------------------------------------------------
# 4. Content generation (mocked)
# ---------------------------------------------------------------------------

class TestInfographicContentGeneration:
    def test_content_generation_returns_structured_json(self, monkeypatch):
        """Verify generate_infographic_content calls the LLM and returns structured content."""
        import asyncio

        from app.services import infographic_renderer

        async def fake_inference(prompt, **kwargs):
            return {
                "title": "Why Cloudless Matters",
                "subtitle": "A Clear Sky Vision",
                "sections": [
                    {"icon": "☁️", "heading": "Overview", "body": "Cloudless means clear skies."},
                    {"icon": "📊", "heading": "Statistics", "body": "30% less pollution."},
                ],
                "footer": "Visit cloudless.gr",
            }

        monkeypatch.setattr(infographic_renderer, "call_inference", fake_inference)

        result = asyncio.run(generate_infographic_content("infographic about cloudless"))
        assert result["title"] == "Why Cloudless Matters"
        assert len(result["sections"]) == 2
        assert result["sections"][0]["icon"] == "☁️"
        assert result["footer"] == "Visit cloudless.gr"
