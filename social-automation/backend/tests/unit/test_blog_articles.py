"""Tests for blog_articles — the markdown-ish format contract the
cloudless.gr blog page renders, plus the social-post link cleanup."""
import re

from app.services.blog_articles import (
    article_markdown_to_html,
    clean_social_post,
    default_slug,
    normalize_article_markdown,
    slugify,
)


class TestSlugify:
    def test_basic(self):
        assert slugify("Weekly Cloud Computing Trends") == "weekly-cloud-computing-trends"

    def test_punctuation_and_runs(self):
        assert slugify("  Hello,  World!! ") == "hello-world"

    def test_default_slug_is_date_prefixed(self):
        slug = default_slug("Weekly Cloud Computing Trends")
        assert re.match(r"^\d{4}-\d{2}-\d{2}-", slug)
        assert slug.endswith("-weekly-cloud-computing-trends")


class TestNormalizeArticleMarkdown:
    def test_drops_title_line(self):
        out = normalize_article_markdown("# Title\n\nIntro para.")
        assert not out.startswith("#")
        assert "Intro para." in out

    def test_downgrades_deep_headings(self):
        out = normalize_article_markdown("### Sub\n\nbody")
        assert "## Sub" in out
        assert "###" not in out

    def test_blank_line_after_heading(self):
        out = normalize_article_markdown("## Head\nBody line")
        assert "## Head\n\nBody line" in out

    def test_blank_line_before_heading(self):
        out = normalize_article_markdown("Body line\n## Head\n\nx")
        assert "Body line\n\n## Head" in out

    def test_list_isolated_into_own_block(self):
        out = normalize_article_markdown("## Sec\n\npara\n- a\n- b\ntrailing")
        blocks = out.split("\n\n")
        assert any(b == "- a\n- b" for b in blocks)
        assert any(b == "trailing" for b in blocks)


class TestArticleMarkdownToHtml:
    def test_headings_paragraphs_lists(self):
        md = "Intro.\n\n## Sec\n\nBody **bold** `code`.\n\n- one\n- two"
        html = article_markdown_to_html(md)
        assert "<p>Intro.</p>" in html
        assert "<h2>Sec</h2>" in html
        assert "<strong>bold</strong>" in html
        assert "<code>code</code>" in html
        assert "<ul>" in html and "<li>one</li>" in html

    def test_links(self):
        html = article_markdown_to_html("see [x](https://a.b/c)")
        assert '<a href="https://a.b/c"' in html

    def test_escapes_html(self):
        html = article_markdown_to_html("a <script>alert(1)</script> b")
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


class TestCleanSocialPost:
    def test_markdown_link_stripped(self):
        out = clean_social_post("hook. [Read more](https://example.com/x). #tag")
        assert "https://" not in out
        assert "Read more" not in out
        assert "#tag" in out

    def test_bare_url_stripped(self):
        out = clean_social_post("check https://cloudless.gr/blog out")
        assert "http" not in out

    def test_read_more_line_removed(self):
        out = clean_social_post("Great stuff\n\nRead more\n\n#tag")
        assert "Read more" not in out
        assert "Great stuff" in out
