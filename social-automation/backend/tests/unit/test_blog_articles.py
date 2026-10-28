"""Tests for blog_articles — the markdown-ish format contract the
cloudless.gr blog page renders, plus the social-post link cleanup."""

import json
import re
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.services import blog_articles as BA
from app.services.blog_articles import (
    article_markdown_to_html,
    assemble_article,
    build_article_prompt,
    clean_social_post,
    default_slug,
    get_published_article,
    normalize_article_markdown,
    publish_article,
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


class TestArticleMarkdownToHtmlEdges:
    def test_blank_blocks_and_trailing_list(self):
        # empty blocks skipped; <ul> closed at end-of-content
        html = article_markdown_to_html("- a\n- b\n\n\n\n")
        assert html.startswith("<ul>")
        assert html.endswith("</ul>")

    def test_list_then_paragraph_closes_list(self):
        html = article_markdown_to_html("- a\n- b\n\npara")
        assert "</ul>\n<p>para</p>" in html

    def test_intermixed_non_bullet_line(self):
        # a block with mixed lines is a paragraph, not a list
        html = article_markdown_to_html("- a\nnot-a-bullet")
        assert "<ul>" not in html
        assert "<p>" in html


class TestBuildArticlePrompt:
    def test_includes_schema_rules_and_categories(self):
        p = build_article_prompt("edge trends")
        assert "edge trends" in p
        assert '## " headings' in p or "ONLY" in p
        for cat in BA.VALID_CATEGORIES:
            assert cat in p

    def test_extra_context_appended(self):
        p = build_article_prompt("t", extra_context="mention R2")
        assert "mention R2" in p
        p2 = build_article_prompt("t", extra_context="   ")
        assert "Additional angle" not in p2


class TestArticleKeyAndBucket:
    def test_article_key(self):
        assert BA.article_key("s") == "newsletter/articles/s.json"

    def test_bucket_missing_raises_503(self, monkeypatch):
        monkeypatch.setattr(BA.settings, "DATALAKE_R2_BUCKET", "  ")
        with pytest.raises(HTTPException) as e:
            BA._datalake_bucket()
        assert e.value.status_code == 503


class TestR2ArticleIO:
    @pytest.mark.asyncio
    async def test_get_published_article(self, monkeypatch):
        monkeypatch.setattr(BA.settings, "DATALAKE_R2_BUCKET", "b")
        monkeypatch.setattr(BA.r2_storage, "get_object", AsyncMock(return_value=b'{"title":"t"}'))
        out = await get_published_article("s")
        assert out["title"] == "t"

    @pytest.mark.asyncio
    async def test_get_published_article_404(self, monkeypatch):
        monkeypatch.setattr(BA.settings, "DATALAKE_R2_BUCKET", "b")
        monkeypatch.setattr(BA.r2_storage, "get_object", AsyncMock(side_effect=HTTPException(404)))
        assert await get_published_article("s") is None

    @pytest.mark.asyncio
    async def test_get_published_article_other_error_reraises(self, monkeypatch):
        monkeypatch.setattr(BA.settings, "DATALAKE_R2_BUCKET", "b")
        monkeypatch.setattr(BA.r2_storage, "get_object", AsyncMock(side_effect=HTTPException(502)))
        with pytest.raises(HTTPException) as e:
            await get_published_article("s")
        assert e.value.status_code == 502

    @pytest.mark.asyncio
    async def test_get_published_article_corrupt_json(self, monkeypatch):
        monkeypatch.setattr(BA.settings, "DATALAKE_R2_BUCKET", "b")
        monkeypatch.setattr(BA.r2_storage, "get_object", AsyncMock(return_value=b"not json{"))
        assert await get_published_article("s") is None

    @pytest.mark.asyncio
    async def test_publish_article_uploads_json(self, monkeypatch):
        monkeypatch.setattr(BA.settings, "DATALAKE_R2_BUCKET", "b")
        up = AsyncMock(return_value={"key": "k"})
        monkeypatch.setattr(BA.r2_storage, "upload_object", up)
        out = await publish_article({"slug": "x", "title": "Τ"})
        assert out == {"key": "k"}
        args, kwargs = up.await_args
        assert args[0] == "newsletter/articles/x.json"
        assert json.loads(args[1].decode())["title"] == "Τ"
        assert kwargs["content_type"] == "application/json"
        assert kwargs["bucket"] == "b"


class TestAssembleArticle:
    def _gen(self, **kw):
        base = {
            "title": "T",
            "excerpt": "e",
            "category": "Analytics",
            "readTime": "7 min read",
            "contentMarkdown": "intro\n\n## s\n\nbody",
            "socialPost": "hook https://x.y #tag",
        }
        base.update(kw)
        return base

    def test_happy_path(self):
        a = assemble_article("slug1", self._gen())
        assert a["slug"] == "slug1" and a["category"] == "Analytics"
        assert a["content"].startswith("intro")
        assert a["html"].startswith("<p>intro</p>")
        assert "https://" not in a["socialPost"]

    def test_missing_title_derives_edition_title(self):
        a = assemble_article("s", self._gen(title="  "), topic="Edge News")
        assert a["title"].startswith("Edge News — ")

    def test_empty_body_raises_502(self):
        with pytest.raises(HTTPException) as e:
            assemble_article("s", self._gen(contentMarkdown="  "))
        assert e.value.status_code == 502

    def test_bad_category_falls_back(self):
        a = assemble_article("s", self._gen(category="NotARealOne"))
        assert a["category"] == "Cloud"

    def test_default_readtime(self):
        a = assemble_article("s", self._gen(readTime=None))
        assert a["readTime"] == "5 min read"
