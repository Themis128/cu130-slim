"""Tests for sanitize_generated_text — model markup artifacts must not publish."""

from app.models.content import Post
from app.services import content_renderer as cr
from app.services.content_renderer import (
    normalize_hashtags,
    render_post_text,
    sanitize_generated_text,
    strip_embedded_metadata,
)


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
        assert strip_embedded_metadata(content, ["cloudless", "devops"], "https://cloudless.gr/") == "Body text"

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
        assert strip_embedded_metadata(content, ["cloudless"], "https://other.gr") == "Intro\n\nwww.cloudless.gr"

    def test_no_metadata_means_no_strip(self):
        content = "Body\n\n#cloudless #devops\nwww.cloudless.gr"
        assert strip_embedded_metadata(content, None, None) == content

    def test_hashtag_only_content_not_blanked(self):
        content = "#cloudless #devops"
        assert strip_embedded_metadata(content, ["cloudless", "devops"], None) == content

    def test_empty_and_none(self):
        assert strip_embedded_metadata("", ["a"], None) == ""
        assert strip_embedded_metadata(None, ["a"], None) is None


class TestRenderPostTextDedupe:
    """Generated copy that already ends with the URL/hashtags must not get
    them appended a second time at publish (regression: duplicated
    cloudless.gr + hashtag block on LinkedIn)."""

    TAGS = ["cloudless", "Cloudflare", "websitebuilding", "webdevelopment", "digitaltools"]
    BODY = (
        "Deploy a site in just 18 minutes. Cloudflare makes it quick, safe, and simple to use.\n\n"
        "www.cloudless.gr\n\n"
        "#cloudless #Cloudflare #websitebuilding #webdevelopment #digitaltools"
    )

    def _post(self, **kw) -> Post:
        kw.setdefault("content_text", self.BODY)
        kw.setdefault("hashtags", self.TAGS)
        kw.setdefault("link_url", "https://www.cloudless.gr")
        return Post(**kw)

    def test_embedded_url_and_tags_not_duplicated_linkedin(self):
        text = render_post_text(self._post(), "linkedin")
        assert text.lower().count("cloudless.gr") == 1
        assert text.count("#cloudless") == 1
        assert text == self.BODY

    def test_embedded_url_and_tags_not_duplicated_facebook(self):
        text = render_post_text(self._post(), "facebook")
        assert text.lower().count("cloudless.gr") == 1
        assert text.count("#webdevelopment") == 1

    def test_missing_tags_still_appended(self):
        post = self._post(content_text="Body only.\n\nwww.cloudless.gr\n\n#cloudless #Cloudflare")
        text = render_post_text(post, "linkedin")
        assert "#websitebuilding" in text
        assert "#webdevelopment" in text
        assert "#digitaltools" in text
        assert text.count("#cloudless") == 1

    def test_tag_prefix_does_not_block_append(self):
        # "#devops" in the body must not satisfy a stored "dev" tag.
        post = self._post(
            content_text="Shipping daily #devops",
            hashtags=["dev"],
            link_url=None,
        )
        text = render_post_text(post, "linkedin")
        assert "#dev" in text

    def test_no_embeds_appends_normally(self):
        post = self._post(content_text="Plain copy with no embeds.")
        text = render_post_text(post, "linkedin")
        assert "https://www.cloudless.gr" in text
        assert "#cloudless" in text

    def test_scheme_and_www_variants_match(self):
        for body_url in ("cloudless.gr", "www.cloudless.gr", "https://cloudless.gr/"):
            post = self._post(content_text=f"Check {body_url} today")
            text = render_post_text(post, "linkedin")
            assert text.count("cloudless.gr") == 1, body_url

    def test_different_host_still_appended(self):
        post = self._post(content_text="Mentions example.com only.")
        text = render_post_text(post, "linkedin")
        assert "example.com" in text
        assert "https://www.cloudless.gr" in text

    def test_similar_host_does_not_match(self):
        post = self._post(
            content_text="mycloudless.gr and blog.cloudless.gr are other sites",
            link_url="https://cloudless.gr",
        )
        text = render_post_text(post, "linkedin")
        assert "https://cloudless.gr" in text

    def test_instagram_never_appends_link(self):
        text = render_post_text(self._post(content_text="No link here."), "instagram")
        assert "cloudless.gr" not in text
        assert "#cloudless" in text

    def test_platform_override_also_deduped(self):
        post = self._post(platform_specific={"linkedin": {"content_text": self.BODY}})
        text = render_post_text(post, "linkedin")
        assert text.lower().count("cloudless.gr") == 1


class TestNormalizeHashtags:
    def test_strips_hash_prefix(self):
        assert normalize_hashtags(["#cloudless", "serverless"]) == ["cloudless", "serverless"]

    def test_strips_repeated_hashes(self):
        assert normalize_hashtags(["##cloudless", "###tag"]) == ["cloudless", "tag"]

    def test_dedupes_case_insensitive(self):
        assert normalize_hashtags(["Cloud", "#cloud", "#CLOUD"]) == ["Cloud"]

    def test_drops_empty_entries(self):
        assert normalize_hashtags(["#", "", "  ", "ok"]) == ["ok"]

    def test_none_and_empty(self):
        assert normalize_hashtags(None) == []
        assert normalize_hashtags([]) == []


class TestMarkupEdgeCases:
    def test_unclosed_hashtag_markup_preserved(self):
        # no closing '}' → break, markup left untouched
        text = "intro {hashtag|\\#|dangling"
        assert sanitize_generated_text(text) == text

    def test_empty_hashtag_markup_scanned_past(self):
        # '{hashtag|\#|}' immediately closed — keep scanning after '{'
        out = sanitize_generated_text("a {hashtag|\\#|} b {hashtag|\\#|real}")
        assert out == "a {hashtag|\\#|} b #real"

    def test_unclosed_placeholder_link_breaks(self):
        text = "click [link to nowhere"
        assert sanitize_generated_text(text) == text


class TestLinkAlreadyPresent:
    def test_empty_inputs(self):
        assert cr._link_already_present("", "cloudless.gr") is False
        assert cr._link_already_present("body", "") is False

    def test_host_without_dot_rejected(self):
        assert cr._link_already_present("go localhost now", "localhost") is False

    def test_no_match_returns_false(self):
        assert cr._link_already_present("totally unrelated copy", "cloudless.gr/x") is False

    def test_host_and_path_match(self):
        assert cr._link_already_present("check www.cloudless.gr/pricing", "https://cloudless.gr/pricing") is True
        assert cr._link_already_present("see mycloudless.gr", "https://cloudless.gr") is False


class TestRenderPostText:
    def _post(self, **kw):
        kw.setdefault("content_text", "Body text")
        kw.setdefault("hashtags", None)
        kw.setdefault("link_url", None)
        kw.setdefault("platform_specific", None)
        return Post(**kw)

    def test_truncates_to_platform_max(self):
        post = self._post(content_text="x" * 400)
        text = render_post_text(post, "twitter")  # 280-char cap
        assert len(text) == 280
        assert text.endswith("…")

    def test_no_content_no_link_early_return(self):
        post = self._post(content_text="", link_url="https://cloudless.gr/x")
        # no body → _link_already_present False → link appended for linkedin
        text = render_post_text(post, "linkedin")
        assert "cloudless.gr/x" in text


class TestRenderHashtags:
    def _post(self, **kw):
        return Post(content_text="t", hashtags=kw.get("hashtags"), platform_specific=None, link_url=None)

    def test_no_hashtags(self):
        assert cr.render_hashtags(self._post(hashtags=None), "linkedin") == []

    def test_hi_zero_platform(self):
        monkeypatch_hints = dict(cr._PLATFORM_HINTS)
        monkeypatch_hints["zero_plat"] = {"ideal_hashtags": (0, 0)}
        post = self._post(hashtags=["a", "b"])
        orig = cr._PLATFORM_HINTS
        cr._PLATFORM_HINTS = monkeypatch_hints
        try:
            assert cr.render_hashtags(post, "zero_plat") == []
        finally:
            cr._PLATFORM_HINTS = orig

    def test_capped_to_ideal(self):
        post = self._post(hashtags=[f"t{i}" for i in range(10)])
        tags = cr.render_hashtags(post, "linkedin")
        assert len(tags) <= 5 and tags[0] == "t0"

    def test_bad_hint_shape_defaults(self):
        orig = cr._PLATFORM_HINTS
        cr._PLATFORM_HINTS = dict(orig, bad_plat={"ideal_hashtags": [1, 2]})
        try:
            assert cr._ideal_hashtag_count("bad_plat") == (0, 5)
        finally:
            cr._PLATFORM_HINTS = orig


def test_render_post_text_truncates(monkeypatch):
    orig = cr._PLATFORM_HINTS
    cr._PLATFORM_HINTS = dict(orig, tiny={"max_chars": 20})
    try:
        post = Post(content_text="x" * 100, hashtags=None, platform_specific=None, link_url=None)
        out = render_post_text(post, "tiny")
        assert out.endswith("…") and len(out) <= 20
    finally:
        cr._PLATFORM_HINTS = orig
