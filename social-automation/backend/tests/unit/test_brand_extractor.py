"""Unit tests for app/services/brand_extractor.py — brand kit extraction.

Covers the SSRF-safe redirect walker, HTML token extraction (colors,
fonts, logo, name, tagline, meta), stylesheet/about-page gathering, and
the CF→DMR inference fallback chain.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest
from bs4 import BeautifulSoup

import app.services.brand_extractor as B
from app.services.url_safety import UnsafeUrlError


def _resp(status=200, text="", headers=None):
    r = Mock()
    r.status_code = status
    r.text = text
    r.headers = headers or {}
    r.raise_for_status = Mock()
    return r


class _Client:
    """httpx.AsyncClient fake — maps url → response via a handler fn."""

    def __init__(self, handler):
        self.handler = handler
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None):
        self.requests.append(url)
        return self.handler(url)


def _identity_url(u: str) -> str:
    return u


def test_get_attr():
    tag = BeautifulSoup('<a href="/x" class="c1 c2">y</a>', "html.parser").a
    assert B._get_attr(tag, "href") == "/x"
    assert B._get_attr(tag, "class") == "c1"  # list attr → first
    assert B._get_attr(tag, "missing") is None
    assert B._get_attr(object(), "href") is None


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def test_extract_brand_name():
    s = _soup("<title>Cloudless — automation consulting</title>")
    assert B._extract_brand_name(s, "cloudless.gr") == "Cloudless"
    s = _soup("<title></title><h1>Big Header</h1>")
    assert B._extract_brand_name(s, "x.io") == "Big Header"
    s = _soup("<body>nothing</body>")
    assert B._extract_brand_name(s, "www.acme.io") == "Acme"


def test_extract_tagline_and_meta():
    s = _soup('<meta name="description" content="  short desc  ">')
    assert B._extract_tagline(s) == "short desc"
    assert B._extract_meta_description(s) == "short desc"

    s = _soup('<meta property="og:description" content="og tagline">')
    assert B._extract_meta_description(s) == "og tagline"

    s = _soup("<h1>T</h1><p>prominent paragraph</p>")
    assert B._extract_tagline(s) == "prominent paragraph"
    assert B._extract_tagline(_soup("<body/>")) is None


def test_extract_colors():
    html = '<style>:root{--primary:#Aa00ff;--accent:rgb(1, 2, 3)}body{color:#ff0000}</style><meta name="theme-color" content="#00FFAA">'
    s = _soup(html)
    colors = B._extract_colors(s, html)
    assert "#aa00ff" in colors
    assert "#010203" in colors  # rgb → hex
    assert "#ff0000" in colors
    assert "#00ffaa" in colors  # theme-color meta
    assert len(colors) <= 6


def test_extract_fonts():
    html = (
        ":root{--font-heading:'Inter', sans-serif; --font-body:\"Work Sans\"}"
        "body{font-family: system-ui, Inter}"
        "h1{font-family: var(--font-heading)}"
        "p{font-family:'Merriweather', serif}"
    )
    fonts = B._extract_fonts(_soup("<body/>"), html)
    assert fonts[0] == "Inter"
    assert "Work Sans" in fonts
    assert "Merriweather" in fonts
    assert "system-ui" not in fonts  # generic skipped


def test_extract_logo():
    s = _soup("<header><img src='/img/logo.png'></header>")
    assert B._extract_logo(s, "https://x.io") == "https://x.io/img/logo.png"
    s = _soup("<body/><link rel='icon' href='/f.ico'>")
    assert B._extract_logo(s, "https://x.io") == "https://x.io/f.ico"
    assert B._extract_logo(_soup("<body/>"), "https://x.io") is None


def test_extract_text_content_strips_boilerplate():
    s = _soup("<body><nav>menu junk</nav><p>real copy</p><script>var x=1;</script><style>a{}</style><footer>footer junk</footer></body>")
    text = B._extract_text_content(s)
    assert "real copy" in text
    assert "menu junk" not in text
    assert "var x" not in text
    assert "footer junk" not in text


# ── async layer ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_safe_get_redirect_chain(monkeypatch):
    monkeypatch.setattr(B, "validate_public_http_url", _identity_url)
    pages = {
        "https://x.io/a": _resp(301, headers={"location": "/b"}),
        "https://x.io/b": _resp(302, headers={"location": "/c"}),
        "https://x.io/c": _resp(200, text="final"),
    }
    client = _Client(lambda u: pages[u])
    r = await B._safe_get(client, "https://x.io/a")
    assert r.status_code == 200 and r.text == "final"
    assert client.requests == ["https://x.io/a", "https://x.io/b", "https://x.io/c"]


@pytest.mark.asyncio
async def test_safe_get_too_many_redirects(monkeypatch):
    monkeypatch.setattr(B, "validate_public_http_url", _identity_url)
    client = _Client(lambda u: _resp(301, headers={"location": u + "x"}))
    with pytest.raises(UnsafeUrlError, match="Too many redirects"):
        await B._safe_get(client, "https://x.io/")


@pytest.mark.asyncio
async def test_fetch_stylesheets_same_origin_only(monkeypatch):
    monkeypatch.setattr(B, "validate_public_http_url", _identity_url)
    html = (
        '<link rel="stylesheet" href="/a.css">'
        '<link rel="stylesheet" href="https://cdn.other.io/b.css">'
        '<link rel="stylesheet" href="/a.css">'  # dedup
        '<link rel="stylesheet" href="/c.css">'
        '<link rel="stylesheet" href="/d.css">'
        '<link rel="stylesheet" href="/e.css">'  # over cap
    )
    s = _soup(html)
    css = {"/a.css": "a{}", "/c.css": "c{}", "/d.css": "d{}", "/e.css": "e{}"}

    def handler(url):
        for p, body in css.items():
            if url.endswith(p):
                return _resp(200, text=body)
        return _resp(404)

    client = _Client(handler)
    out = await B._fetch_stylesheets(client, s, "https://x.io")
    assert out == ["a{}", "c{}", "d{}"]  # same-origin, deduped, capped at 3
    assert not any(u.endswith("/e.css") for u in client.requests)
    assert not any("other.io" in u for u in client.requests)


@pytest.mark.asyncio
async def test_first_about_page(monkeypatch):
    monkeypatch.setattr(B, "validate_public_http_url", _identity_url)
    big = "about page " + "x" * 600

    def handler(url):
        if url.endswith("/about"):
            return _resp(404)
        if url.endswith("/about-us"):
            return _resp(200, text=big)
        return _resp(200, text="tiny")

    client = _Client(handler)
    assert (await B._first_about_page(client, "https://x.io")) == big

    # all fail → ""
    client2 = _Client(lambda u: _resp(404))
    assert await B._first_about_page(client2, "https://x.io") == ""


@pytest.mark.asyncio
async def test_analyze_website_content(monkeypatch):
    assert await B._analyze_website_content("   ", "x", None) == {}

    ai = {"industry": "SaaS", "mission": "automate all the things"}
    call = AsyncMock(return_value={"response": ai})
    monkeypatch.setattr(B, "call_inference", call)
    out = await B._analyze_website_content("site copy", "Cloudless", "tag")
    assert out == ai
    assert call.await_args_list[0].kwargs["provider_name"] == "cloudflare"

    # cloudflare fails → dmr fallback used
    call2 = AsyncMock(side_effect=[RuntimeError("cf down"), {"industry": "SaaS"}])
    monkeypatch.setattr(B, "call_inference", call2)
    out2 = await B._analyze_website_content("copy", "x", None)
    assert out2 == {"industry": "SaaS"}
    providers = [c.kwargs["provider_name"] for c in call2.await_args_list]
    assert providers == ["cloudflare", "dmr"]

    # both fail → {}
    monkeypatch.setattr(B, "call_inference", AsyncMock(side_effect=RuntimeError("dead")))
    assert await B._analyze_website_content("copy", "x", None) == {}


@pytest.mark.asyncio
async def test_extract_brand_from_url(monkeypatch):
    monkeypatch.setattr(B, "validate_public_http_url", _identity_url)
    homepage = """
    <html><head>
      <title>Cloudless — automation</title>
      <meta name="description" content="We automate ops">
      <meta name="theme-color" content="#112233">
      <link rel="stylesheet" href="/main.css">
    </head><body>
      <header><img src="/logo.png"></header>
      <h1>Cloudless</h1>
      <p>Plain-English automation for real teams.</p>
      <style>body{color:#445566;font-family:'Inter'}</style>
    </body></html>"""
    about = "<body><p>" + "About us detail. " * 60 + "</p></body>"

    def handler(url):
        if url.endswith("/main.css"):
            return _resp(200, text=":root{--font-b:'Work Sans'}")
        if url.endswith(("/about", "/about-us", "/en/about")):
            return _resp(200, text=about) if url.endswith("/about") else _resp(404)
        return _resp(200, text=homepage)

    monkeypatch.setattr(B.httpx, "AsyncClient", lambda **kw: _Client(handler))
    monkeypatch.setattr(
        B,
        "call_inference",
        AsyncMock(
            return_value={
                "industry": "automation",
                "positioning_statement": "pos",
                "mission": "mission",
                "values": ["v1"],
                "competitor_names": ["c1"],
                "target_audience": {"demographics": "smb"},
                "tone_dimensions": {"formality": 3},
                "messaging_pillars": [{"pillar": "p"}],
                "banned_phrases": ["b"],
                "preferred_phrases": ["p"],
                "voice_signature": {"tone": "direct"},
                "image_style": "clean",
                "photography_direction": "minimal",
            }
        ),
    )

    result = await B.extract_brand_from_url("https://cloudless.gr")
    assert result["name"] == "Cloudless"
    assert result["tagline"] == "We automate ops"
    assert result["industry"] == "automation"
    assert result["visual"]["primary_color"] in result["visual"]["primary_color"]
    assert result["visual"]["logo_url"] == "https://cloudless.gr/logo.png"
    assert result["visual"]["font_heading"] is not None
    assert result["voice"]["messaging_pillars"] == [{"pillar": "p"}]
    assert result["voice"]["example_content"].startswith("Cloudless")


@pytest.mark.asyncio
async def test_extract_brand_from_url_http_error(monkeypatch):
    monkeypatch.setattr(B, "validate_public_http_url", _identity_url)
    client = _Client(lambda u: _resp(500, text="oops"))
    monkeypatch.setattr(B.httpx, "AsyncClient", lambda **kw: client)
    with pytest.raises(UnsafeUrlError, match="HTTP 500"):
        await B.extract_brand_from_url("https://x.io")


@pytest.mark.asyncio
async def test_extract_brand_no_colors(monkeypatch):
    """Pages with zero detectable colors → no 'visual' key."""
    monkeypatch.setattr(B, "validate_public_http_url", _identity_url)

    def handler(url):
        if "/about" in url:
            return _resp(404)
        return _resp(200, text="<title>Nocolor</title><body><p>text only</p>")

    monkeypatch.setattr(B.httpx, "AsyncClient", lambda **kw: _Client(handler))
    monkeypatch.setattr(B, "call_inference", AsyncMock(return_value={}))
    result = await B.extract_brand_from_url("https://x.io")
    assert result["name"] == "Nocolor"
    assert "visual" not in result
    assert result["voice"]["messaging_pillars"] == []
