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


class TestStripEmbeddedMetadata:
    def test_trailing_hashtag_line_stripped(self):
        content = "Check our new feature\n\n#cloudless #serverless"
        assert strip_embedded_metadata(content, ["cloudless", "serverless"], None) == "Check our new feature"

    def test_trailing_url_line_stripped(self):
        content = "Big news today\n\nwww.cloudless.gr"
        assert strip_embedded_metadata(content, None, "https://www.cloudless.gr") == "Big news today"

    def test_both_trailing_blocks_stripped(self):
        content = "Body text\n\nwww.cloudless.gr\n\n#cloudless #devops"
        assert (
            strip_embedded_metadata(content, ["cloudless", "devops"], "https://cloudless.gr/")
            == "Body text"
        )

    def test_clean_content_unchanged(self):
        content = "No embeds here."
        assert strip_embedded_metadata(content, ["a"], "https://x.gr") == content

    def test_hashtag_line_with_foreign_tag_kept(self):
        # A trailing line containing a tag NOT in metadata is user content — keep it.
        content = "Body\n\n#cloudless #unrelated"
        assert strip_embedded_metadata(content, ["cloudless"], None) == content

    def test_inline_hashtags_preserved(self):
        content = "We love #cloudless and #devops daily"
        assert strip_embedded_metadata(content, ["cloudless", "devops"], None) == content

    def test_inline_url_preserved(self):
        content = "Visit www.cloudless.gr for details"
        assert strip_embedded_metadata(content, None, "https://cloudless.gr") == content

    def test_mid_body_url_line_preserved(self):
        # Only trailing lines are stripped — a standalone URL mid-body stays.
        content = "Intro\n\nwww.cloudless.gr\n\n#cloudless"
        assert (
            strip_embedded_metadata(content, ["cloudless"], "https://other.gr")
            == "Intro\n\nwww.cloudless.gr"
        )

    def test_no_metadata_means_no_strip(self):
        content = "Body\n\n#cloudless #devops\nwww.cloudless.gr"
        assert strip_embedded_metadata(content, None, None) == content

    def test_hashtag_only_content_not_blanked(self):
        content = "#cloudless #devops"
        assert strip_embedded_metadata(content, ["cloudless", "devops"], None) == content

    def test_empty_and_none(self):
        assert strip_embedded_metadata("", ["a"], None) == ""
        assert strip_embedded_metadata(None, ["a"], None) is None
