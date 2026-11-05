"""Coverage for site_audit — fetch exceptions, detect helpers, run flow."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.site_audit as SA

pytestmark = pytest.mark.asyncio


async def test_get_exception_returns_none():
    client = SimpleNamespace(get=AsyncMock(side_effect=Exception("net down")))
    assert await SA._get(client, "https://x.gr") is None


def test_detect_tech_headers_and_dedupe():
    class H(dict):
        get = dict.get

    found = SA._detect_tech("<html><script>wp</script></html>", H({"x-powered-by": "PHP/8", "server": "nginx"}))
    assert any("powered-by" in f for f in found) and any("server:" in f for f in found)
    assert len(found) == len(set(found))


def test_seo_title_branches():
    missing = SA._seo_checks("<html></html>")
    assert any("Missing" in f["finding"] for f in missing)
    short = SA._seo_checks("<title>Hi</title>")
    assert any("length" in f["finding"].lower() for f in short)


async def test_run_audit_redirect_and_plan(monkeypatch):
    html = "<title>A proper length title for SEO here</title><body></body>"

    class Resp:
        def __init__(self, is_redirect=False, loc=None):
            self.is_redirect = is_redirect
            self.headers = {"location": loc} if loc else {}
            self.text = html
            self.content = html.encode()
            self.url = "https://x.gr"
            self.status_code = 301 if is_redirect else 200

    class C:
        def __init__(self):
            self.calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kw):
            self.calls += 1
            return Resp(is_redirect=self.calls == 1, loc="https://y.gr") if self.calls == 1 else Resp()

    monkeypatch.setattr(SA.httpx, "AsyncClient", lambda **kw: C())
    monkeypatch.setattr(SA, "validate_public_http_url", lambda u: u)
    out = await SA.run_site_audit("https://x.gr")
    assert out["url"] and out["score"] is not None and "findings" in out


async def test_run_audit_unreachable(monkeypatch):
    import httpx

    class C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kw):
            raise httpx.ConnectError("dead")

    monkeypatch.setattr(SA.httpx, "AsyncClient", lambda **kw: C())
    monkeypatch.setattr(SA, "validate_public_http_url", lambda u: u)
    with pytest.raises(RuntimeError, match="unreachable"):
        await SA.run_site_audit("https://dead.gr")
