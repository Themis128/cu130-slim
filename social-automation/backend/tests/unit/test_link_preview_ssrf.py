"""SSRF guard: every redirect hop is re-validated; tailnet/metadata IPs refused."""

import asyncio
import socket

import httpx
import pytest

import app.services.link_preview as lp

_HOSTS = {
    "public.example": "93.184.216.34",
    "evil.example": "93.184.216.35",
    "meta.internal": "169.254.169.254",
    "tail.example": "100.74.191.58",
}


def fake_gai(host, *args, **kwargs):
    # Literal IPs (e.g. the pinned request URL after a relative redirect)
    # resolve to themselves, like real getaddrinfo.
    return [(socket.AF_INET, 0, 0, "", (_HOSTS.get(host, host), 0))]


def run(handler, url):
    orig = httpx.AsyncClient

    class MockClient(orig):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    lp.httpx.AsyncClient = MockClient
    try:
        return asyncio.run(lp.fetch_link_preview(url))
    finally:
        lp.httpx.AsyncClient = orig


@pytest.fixture(autouse=True)
def _fake_dns(monkeypatch):
    monkeypatch.setattr(lp.socket, "getaddrinfo", fake_gai)


def test_ok():
    html = '<html><head><meta property="og:title" content="Hi"></head></html>'
    result = run(lambda req: httpx.Response(200, html=html), "https://public.example/x")
    assert result["title"] == "Hi"
    assert result["url"] == "https://public.example/x"


def test_redirect_public_ok():
    def handler(req):
        # Requests connect to the validated IP (DNS-rebinding TOCTOU fix) —
        # the Host header still carries the original hostname.
        if req.url.host == "93.184.216.34":
            assert req.headers["host"] == "public.example"
            return httpx.Response(302, headers={"location": "https://evil.example/y"})
        return httpx.Response(200, html="<title>T</title>")

    assert run(handler, "https://public.example/")["url"] == "https://evil.example/y"


def test_redirect_to_metadata_blocked():
    def handler(req):
        return httpx.Response(302, headers={"location": "http://meta.internal/latest"})

    with pytest.raises(ValueError):
        run(handler, "https://public.example/")


def test_tailnet_blocked():
    with pytest.raises(ValueError):
        run(lambda req: httpx.Response(200), "http://tail.example/")


def test_redirect_loop_capped():
    def handler(req):
        return httpx.Response(302, headers={"location": "/again"})

    with pytest.raises(ValueError, match="Too many"):
        run(handler, "https://public.example/")


def test_resolve_public_ip_guards(monkeypatch):
    def boom(*_a, **_k):
        raise socket.gaierror("fail")

    monkeypatch.setattr(lp.socket, "getaddrinfo", boom)
    with pytest.raises(ValueError, match="Cannot resolve"):
        lp._resolve_public_ip("nope.example")

    monkeypatch.setattr(lp.socket, "getaddrinfo", lambda *_a, **_k: [])
    with pytest.raises(ValueError, match="Cannot resolve"):
        lp._resolve_public_ip("empty.example")

    monkeypatch.setattr(
        lp.socket,
        "getaddrinfo",
        lambda *_a, **_k: [(socket.AF_INET6, 0, 0, "", ("::ffff:127.0.0.1", 0, 0, 0))],
    )
    with pytest.raises(ValueError, match="not publicly routable"):
        lp._resolve_public_ip("mapped.example")

    monkeypatch.setattr(
        lp.socket,
        "getaddrinfo",
        lambda *_a, **_k: [(socket.AF_INET, 0, 0, "", ("224.0.0.1", 0))],
    )
    with pytest.raises(ValueError, match="not publicly routable"):
        lp._resolve_public_ip("multi.example")


def test_redirect_without_location():
    def handler(_req):
        return httpx.Response(302, headers={})

    with pytest.raises(ValueError, match="Redirect without"):
        run(handler, "https://public.example/")


def test_protocol_relative_and_root_relative_image():
    html = '<html><head><meta property="og:image" content="//cdn.example/a.png"></head></html>'
    result = run(lambda _req: httpx.Response(200, html=html), "https://public.example/x")
    assert result["image"] == "https://cdn.example/a.png"

    html = '<html><head><meta property="og:image" content="/img.png"></head></html>'
    result = run(lambda _req: httpx.Response(200, html=html), "https://public.example/x")
    assert result["image"] == "https://public.example/img.png"


# ── Stealth render fallback (cloak-fallback pattern via the bridge) ────


def test_is_challenge_detects_interstitials():
    assert lp._is_challenge(b"<title>Just a moment...</title>") is True
    assert lp._is_challenge(b"<script>var cf_chl_opt={};</script>") is True
    assert lp._is_challenge(b"<html><head><title>Blog post</title></head></html>") is False


class _FakeBridge:
    """Stands in for the bridge container: returns rendered DOM."""

    def __init__(self, *_a, **_k):
        self.last_url = None

    async def navigate(self, url):
        self.last_url = url

    async def evaluate(self, _expr):
        import json

        html = (
            "<html><head>"
            '<meta property="og:title" content="Rendered Title">'
            '<meta property="og:description" content="Rendered desc">'
            "</head><body>app</body></html>"
        )
        return {"result": json.dumps({"html": html, "url": "https://public.example/x"})}


class _FakeSession:
    """Stands in for the Redis-locked browser session context."""

    def __init__(self, bridge):
        self._bridge = bridge

    async def __aenter__(self):
        return self._bridge

    async def __aexit__(self, *_a):
        return None


@pytest.fixture()
def fake_render(monkeypatch):
    import app.services.browser_bridge as bb
    import app.services.browser_orchestrator as bo

    bridge = _FakeBridge()
    monkeypatch.setattr(bb, "BrowserBridgeClient", lambda *_a, **_k: bridge)
    monkeypatch.setattr(bo, "browser_session", lambda *_a, **_k: _FakeSession(bridge))
    monkeypatch.setenv("BROWSER_BRIDGE_URL", "http://bridge:9223")
    return bridge


def test_blocked_status_uses_render_fallback(fake_render, monkeypatch):
    from app.core.config import get_settings

    get_settings.cache_clear()  # pick up BROWSER_BRIDGE_URL env
    try:
        result = run(
            lambda _req: httpx.Response(403, html="<title>Just a moment...</title>"),
            "https://public.example/x",
        )
    finally:
        get_settings.cache_clear()
    assert result["title"] == "Rendered Title"
    assert fake_render.last_url == "https://public.example/x"


def test_rendered_preview_parses_og_tags(fake_render, monkeypatch):
    import asyncio

    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        out = asyncio.run(lp._rendered_preview("https://public.example/x"))
    finally:
        get_settings.cache_clear()
    assert out["title"] == "Rendered Title"
    assert out["description"] == "Rendered desc"


def test_empty_preview_triggers_render(fake_render, monkeypatch):
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        # 200 but a bare JS shell — no title/description → render path.
        result = run(
            lambda _req: httpx.Response(200, html="<html><body><div id=app></div></body></html>"),
            "https://public.example/x",
        )
    finally:
        get_settings.cache_clear()
    assert result["title"] == "Rendered Title"


@pytest.mark.asyncio
async def test_rendered_preview_no_bridge_url_returns_none(monkeypatch):
    from types import SimpleNamespace

    import app.core.config as cfg

    monkeypatch.setattr(cfg, "get_settings", lambda: SimpleNamespace(BROWSER_BRIDGE_URL=""))
    assert await lp._rendered_preview("https://public.example/x") is None


class _BridgeNoHtml:
    async def navigate(self, url):
        pass

    async def evaluate(self, _expr):
        return {"result": '{"url": "https://public.example/x"}'}  # no html


class _BridgeEmptyPreview:
    async def navigate(self, url):
        pass

    async def evaluate(self, _expr):
        import json

        html = "<html><body><div>plain page, no meta</div></body></html>"
        return {"result": json.dumps({"html": html, "url": "https://public.example/x"})}


def _wire_bridge(monkeypatch, bridge):
    import app.services.browser_bridge as bb
    import app.services.browser_orchestrator as bo

    monkeypatch.setattr(bb, "BrowserBridgeClient", lambda *_a, **_k: bridge)
    monkeypatch.setattr(bo, "browser_session", lambda *_a, **_k: _FakeSession(bridge))
    monkeypatch.setenv("BROWSER_BRIDGE_URL", "http://bridge:9223")


@pytest.mark.asyncio
async def test_rendered_preview_missing_html_returns_none(monkeypatch):
    from app.core.config import get_settings

    _wire_bridge(monkeypatch, _BridgeNoHtml())
    get_settings.cache_clear()
    try:
        assert await lp._rendered_preview("https://public.example/x") is None
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_rendered_preview_empty_extract_returns_none(monkeypatch):
    from app.core.config import get_settings

    _wire_bridge(monkeypatch, _BridgeEmptyPreview())
    get_settings.cache_clear()
    try:
        assert await lp._rendered_preview("https://public.example/x") is None
    finally:
        get_settings.cache_clear()


def test_challenge_body_uses_render_fallback(fake_render, monkeypatch):
    """A 200 that IS a challenge/interstitial page → rendered preview returned."""
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        result = run(
            lambda _req: httpx.Response(200, html="<title>Just a moment...</title>"),
            "https://public.example/x",
        )
    finally:
        get_settings.cache_clear()
    assert result["title"] == "Rendered Title"
    assert fake_render.last_url == "https://public.example/x"
