"""Unit tests for UTM tagging of owned-domain links."""

from app.services.utm import tag_owned_links, tracked_url

KW = {"source": "linkedin", "medium": "organic_social", "campaign": "cmp", "content": "post-1"}


def test_tracked_url_tags_owned_host():
    out = tracked_url("https://cloudless.gr/contact", **KW)
    assert "utm_source=linkedin" in out
    assert "utm_medium=organic_social" in out
    assert "utm_campaign=cmp" in out
    assert "utm_content=post-1" in out


def test_tracked_url_subdomain_and_path_preserved():
    out = tracked_url("https://social.cloudless.gr/pricing?utm_source=x&ref=y", **KW)
    assert "ref=y" in out
    assert "utm_source=x" in out  # author-set params never clobbered


def test_tracked_url_passes_external_through():
    for url in (
        "https://example.com/x",
        "https://linkedin.com/company/cloudless-gr",
        "https://cloudless.gr.evil.com/phish",
        "not-a-url",
        "",
    ):
        assert tracked_url(url, **KW) == url


def test_tracked_url_is_idempotent():
    once = tracked_url("https://cloudless.gr/contact", **KW)
    twice = tracked_url(once, **{**KW, "source": "facebook"})
    assert "utm_source=linkedin" in twice  # first tag wins


def test_tag_owned_links_rewrites_only_owned_urls():
    text = "Book a free audit: https://cloudless.gr/contact — or see https://example.com for details."
    out = tag_owned_links(text, platform="instagram", campaign="c", content="p9")
    assert "utm_source=instagram" in out
    assert "example.com?" not in out  # external link untouched


def test_tag_owned_links_strips_trailing_punctuation():
    out = tag_owned_links("Visit https://cloudless.gr/pricing.", platform="x", campaign="c", content="p")
    assert out.endswith(".")
    assert "%3Futm" not in out.split(".")[-1]
    assert "pricing?utm_" in out


def test_tag_owned_links_no_owned_host_is_noop():
    text = "No links here, just https://example.com/x"
    assert tag_owned_links(text, platform="x", campaign="c", content="p") == text


def test_urlsplit_value_error_returns_url_unchanged():
    """Invalid bracketed URLs raise in urlsplit — passed through untouched."""
    bad = "http://[unclosed"
    assert tracked_url(bad, source="x", medium="y", campaign="z", content="c") == bad
