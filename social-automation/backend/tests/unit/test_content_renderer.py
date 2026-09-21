"""Tests for sanitize_generated_text — model markup artifacts must not publish."""
from app.services.content_renderer import sanitize_generated_text, strip_embedded_metadata


class TestSanitizeGeneratedText:
    def test_hashtag_markup_converted(self):
        text = "Great post {hashtag|\\#|cloudless} {hashtag|\\#|serverless}"
        assert sanitize_generated_text(text) == "Great post #cloudless #serverless"

    def test_hashtag_markup_leading(self):
        text = "{hashtag|\\#|cloudless} {hashtag|\\#|serverless}\nCloudless.gr - fast."
        assert sanitize_generated_text(text) == "#cloudless #serverless\nCloudless.gr - fast."

    def test_placeholder_link_removed(self):
        text = "Click the link [link to Cloudless website] to learn more"
        assert sanitize_generated_text(text) == "Click the link to learn more"

    def test_placeholder_links_multiple(self):
        text = "Done! [link to Cloudless website] [link to Cloudless case studies], more"
        assert sanitize_generated_text(text) == "Done! , more".replace(" ,", ",")

    def test_clean_text_unchanged(self):
        text = "Normal post text\n\n#real #hashtags\nwww.cloudless.gr"
        assert sanitize_generated_text(text) == text

    def test_empty_and_none(self):
        assert sanitize_generated_text("") == ""
        assert sanitize_generated_text(None) is None or sanitize_generated_text(None) == ""

    def test_whitespace_collapsed_after_removal(self):
        text = "a   [link to x]   b"
        assert sanitize_generated_text(text) == "a b"
