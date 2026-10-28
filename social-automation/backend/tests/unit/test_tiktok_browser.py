"""Unit tests for app/services/tiktok_browser.py — sidecar HTTP client.

httpx.AsyncClient is faked at the module boundary; the request plumbing,
validation, and error mapping are the real code.
"""

from __future__ import annotations

import httpx
import pytest

from app.services.tiktok_browser import TikTokBrowserError, TikTokBrowserService


class _Resp:
    def __init__(self, status=200, body=None, text="ok"):
        self.status_code = status
        self._body = body if body is not None else {}
        self.text = text

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _Client:
    def __init__(self, resp=None, raise_exc=None):
        self.resp = resp or _Resp()
        self.raise_exc = raise_exc
        self.calls = []
        self.is_closed = False

    async def request(self, method, url, json=None):
        self.calls.append((method, url, json))
        if self.raise_exc:
            raise self.raise_exc
        return self.resp

    async def aclose(self):
        self.is_closed = True


@pytest.fixture
def svc(monkeypatch):
    s = TikTokBrowserService("http://sidecar:9999/")
    return s


def test_base_url_trailing_slash():
    assert TikTokBrowserService("http://x/").base_url == "http://x"


@pytest.mark.asyncio
async def test_client_lazy_and_close(svc):
    c1 = svc.client
    assert c1 is svc.client  # cached
    await svc.close()
    c2 = svc.client  # closed → new client
    assert c2 is not c1
    await svc.close()


@pytest.mark.asyncio
async def test_health_and_session(svc):
    fake = _Client(_Resp(200, {"ok": True}))
    svc._client = fake
    assert await svc.health() == {"ok": True}
    out = await svc.set_session("sid", "uid")
    assert out == {"ok": True}
    method, url, body = fake.calls[-1]
    assert (method, url) == ("POST", "http://sidecar:9999/session")
    assert body == {"session_id": "sid", "user_id": "uid"}
    await svc.check_session()
    assert fake.calls[-1][1].endswith("/session")


@pytest.mark.asyncio
async def test_request_connect_error(svc):
    svc._client = _Client(raise_exc=httpx.ConnectError("refused"))
    with pytest.raises(TikTokBrowserError) as e:
        await svc.health()
    assert e.value.status_code == 503


@pytest.mark.asyncio
async def test_request_http_error_json_and_text(svc):
    svc._client = _Client(_Resp(400, {"error": "bad payload"}))
    with pytest.raises(TikTokBrowserError) as e:
        await svc.health()
    assert e.value.status_code == 400 and e.value.detail == "bad payload"

    # non-JSON error body → raw text fallback
    svc._client = _Client(_Resp(500, ValueError("no json"), text="raw err"))
    with pytest.raises(TikTokBrowserError) as e:
        await svc.health()
    assert e.value.detail == "raw err"


@pytest.mark.asyncio
async def test_privacy_endpoints(svc):
    fake = _Client(_Resp(200, {"done": 1}))
    svc._client = fake
    await svc.read_profile()
    await svc.set_private_account(True)
    assert fake.calls[-1][1].endswith("/privacy/private-account")
    assert fake.calls[-1][2] == {"enabled": True}

    await svc.set_comments("Everyone")
    assert fake.calls[-1][2] == {"permission": "Everyone"}
    with pytest.raises(TikTokBrowserError) as e:
        await svc.set_comments("Nobody")
    assert e.value.status_code == 400

    await svc.set_direct_messages("Friends", "Message request")
    assert fake.calls[-1][2]["potential_connections"] == "Friends"


@pytest.mark.asyncio
async def test_notifications_ads_accessibility(svc):
    fake = _Client(_Resp())
    svc._client = fake
    await svc.set_desktop_notifications(False)
    assert fake.calls[-1][2] == {"enabled": False}
    await svc.set_interaction_notifications(likes=True, mentions_and_tags=False)
    body = fake.calls[-1][2]
    assert body["likes"] is True and body["comments"] is None
    assert body["mentions_and_tags"] is False
    await svc.set_personalized_ads(True)
    assert fake.calls[-1][1].endswith("/ads/personalized")
    await svc.set_color_contrast(True)
    assert fake.calls[-1][1].endswith("/accessibility/contrast")


@pytest.mark.asyncio
async def test_business_verification_and_settings(svc):
    fake = _Client(_Resp(200, {"status": "pending"}))
    svc._client = fake
    await svc.fill_business_verification(
        company_name="Cloudless", website="https://cloudless.gr",
        country="GR", address="x", industry="tech",
        business_license_number="123")
    body = fake.calls[-1][2]
    assert body["company_name"] == "Cloudless"
    assert body["business_license_number"] == "123"
    out = await svc.get_business_verification_status()
    assert out == {"status": "pending"}
    await svc.read_all_settings()
    assert fake.calls[-1][1].endswith("/settings")
