"""Coverage for twitter_api OAuth1 request wrapper + _send fallback."""

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.twitter_api as T

pytestmark = pytest.mark.asyncio


def _client(access_token="bearer", media_signer=object()):
    return T.TwitterAPIClient(access_token=access_token, media_signer=media_signer)


def _fake_resp(status=200, payload=None, text="ok"):
    return SimpleNamespace(status_code=status, headers={"h": "v"}, text=text,
                           json=lambda: payload or {"id": 1})


def test_oauth1_request_wrapper(monkeypatch):
    fake = ModuleType("requests_oauthlib")
    fake.OAuth1Session = lambda *a: SimpleNamespace(
        request=lambda method, url, **kw: _fake_resp(201, {"k": 1}, "txt"))
    monkeypatch.setitem(sys.modules, "requests_oauthlib", fake)

    c = _client()
    c._ak, c._as, c._at, c._ats = "a", "s", "t", "ts"
    r = c._oauth1_request("POST", "https://api.x.com/2/tweets", {"json": {"text": "hi"}, "junk": 1})
    assert r.status_code == 201 and r.headers == {"h": "v"} and r.text == "txt" and r.json() == {"k": 1}


async def test_send_401_oauth1_fallback(monkeypatch):
    c = _client()
    c._ak = "a"
    monkeypatch.setattr(c, "_oauth1_request", lambda m, u, kw: _fake_resp(200, {"ok": True}))
    client = SimpleNamespace(post=AsyncMock(return_value=_fake_resp(401)))
    resp = await c._send(client, "POST", "https://api.x.com/2/tweets", json={"x": 1})
    assert resp.json() == {"ok": True}


async def test_send_401_no_fallback_when_disabled():
    c = _client()
    c._ak = "a"
    c._oauth1_fallback = False
    client = SimpleNamespace(post=AsyncMock(return_value=_fake_resp(401)))
    resp = await c._send(client, "POST", "https://api.x.com/2/tweets")
    assert resp.status_code == 401


async def test_send_200_no_retry():
    c = _client()
    client = SimpleNamespace(get=AsyncMock(return_value=_fake_resp(200)))
    resp = await c._send(client, "GET", "https://api.x.com/2/me")
    assert resp.status_code == 200 and client.get.await_count == 1
