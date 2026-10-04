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
