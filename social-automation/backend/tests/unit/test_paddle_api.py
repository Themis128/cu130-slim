"""Tests for app/services/paddle_api.py — Paddle Billing v2 client."""
from __future__ import annotations

import hashlib
import hmac
import time
from types import SimpleNamespace

import pytest

import app.services.paddle_api as pa


def _settings(**kw):
    base = dict(
        PADDLE_API_KEY="key",
        PADDLE_CLIENT_TOKEN="ctok",
        PADDLE_WEBHOOK_SECRET="whsec",
        paddle_api_base="https://api.paddle.test",
        FRONTEND_URL="https://app.test",
        paddle_price_tiers={"price_pro": "pro"},
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _patch_settings(monkeypatch):
    monkeypatch.setattr(pa, "_settings", lambda: _settings())


class _Resp:
    def __init__(self, code=200, body=None):
        self.status_code = code
        self._body = body
        self.text = "resp-text"

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class _HTTP:
    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self.resp


def _wire(monkeypatch, http):
    monkeypatch.setattr(pa.httpx, "AsyncClient", lambda **kw: http)


def test_settings_passthrough(monkeypatch):
    # undo the autouse patch to exercise the real get_settings passthrough
    monkeypatch.undo()
    import app.services.paddle_api as m
    assert m._settings() is not None


def test_configured(monkeypatch):
    assert pa.paddle_configured() is True
    monkeypatch.setattr(pa, "_settings", lambda: _settings(PADDLE_API_KEY=""))
    assert pa.paddle_configured() is False


@pytest.mark.asyncio
async def test_request_ok_and_errors(monkeypatch):
    http = _HTTP(_Resp(200, {"data": {"id": "1"}}))
    _wire(monkeypatch, http)
    out = await pa._request("GET", "/x", params={"a": 1})
    assert out == {"data": {"id": "1"}}
    method, url, kw = http.calls[0]
    assert url == "https://api.paddle.test/x"
    assert kw["headers"]["Authorization"] == "Bearer key"

    # non-JSON body, ok status
    _wire(monkeypatch, _HTTP(_Resp(200, None)))
    assert await pa._request("GET", "/x") == {}

    # error with detail
    _wire(monkeypatch, _HTTP(_Resp(400, {"error": {"detail": "bad thing"}})))
    with pytest.raises(pa.PaddleError, match="bad thing"):
        await pa._request("GET", "/x")

    # error without detail -> falls back to resp.text
    _wire(monkeypatch, _HTTP(_Resp(500, None)))
    with pytest.raises(pa.PaddleError, match="resp-text"):
        await pa._request("GET", "/x")


# ── webhook signature ────────────────────────────────────────────────


def _sig(ts: int, body: bytes, secret: str = "whsec") -> str:
    digest = hmac.new(secret.encode(), f"{ts}:".encode() + body, hashlib.sha256).hexdigest()
    return f"ts={ts};h1={digest}"


def test_verify_signature(monkeypatch):
    body = b'{"event":"x"}'
    ts = int(time.time())
    assert pa.verify_webhook_signature(body, _sig(ts, body)) is True

    # wrong h1
    assert pa.verify_webhook_signature(body, f"ts={ts};h1={'0'*64}") is False
    # stale ts (>5min)
    assert pa.verify_webhook_signature(body, _sig(ts - 400, body)) is False
    # malformed header
    assert pa.verify_webhook_signature(body, "garbage") is False
    assert pa.verify_webhook_signature(body, "") is False
    # no secret
    monkeypatch.setattr(pa, "_settings", lambda: _settings(PADDLE_WEBHOOK_SECRET=""))
    assert pa.verify_webhook_signature(body, _sig(ts, body)) is False


# ── customers / transactions / subscriptions / portal ────────────────


@pytest.mark.asyncio
async def test_get_or_create_customer(monkeypatch):
    calls = []

    async def fake_req(method, path, **kw):
        calls.append((method, path))
        if method == "GET":
            return {"data": [{"id": "c1", "custom_data": {"team_id": "other"}},
                             {"id": "c2", "custom_data": {"team_id": "t1"}}]}
        return {"data": {"id": "c_new"}}

    monkeypatch.setattr(pa, "_request", fake_req)
    # existing match returned
    assert await pa.get_or_create_customer("t1", "e@x") == "c2"

    async def fake_req2(method, path, **kw):
        if method == "GET":
            return {"data": []}
        return {"data": {"id": "c_new"}}

    monkeypatch.setattr(pa, "_request", fake_req2)
    assert await pa.get_or_create_customer("t1", "e@x", name="N") == "c_new"


@pytest.mark.asyncio
async def test_create_checkout_transaction(monkeypatch):
    captured = {}

    async def fake_req(method, path, **kw):
        captured.update(kw.get("json") or {})
        return {"data": {"id": "txn1", "checkout": {"url": "https://checkout"}}}

    monkeypatch.setattr(pa, "_request", fake_req)
    out = await pa.create_checkout_transaction(
        price_id="p1", team_id="t1", customer_id="c1")
    assert out == {"id": "txn1", "checkout_url": "https://checkout"}
    assert captured["customer_id"] == "c1"
    assert captured["custom_data"] == {"team_id": "t1"}
    assert "checkout=success" in captured["checkout"]["url"]

    captured.clear()
    await pa.create_checkout_transaction(
        price_id="p1", team_id="t1", customer_email="e@x")
    assert captured["customer"] == {"email": "e@x"}

    captured.clear()

    async def fake_req2(method, path, **kw):
        captured.update(kw.get("json") or {})
        return {"data": {"id": "t2", "checkout": None}}

    monkeypatch.setattr(pa, "_request", fake_req2)
    out2 = await pa.create_checkout_transaction(price_id="p", team_id="t")
    assert out2["checkout_url"] is None
    assert "customer_id" not in captured and "customer" not in captured


@pytest.mark.asyncio
async def test_subscription_and_portal(monkeypatch):
    monkeypatch.setattr(pa, "_request", lambda m, p, **kw: _sub(m, p))

    async def _sub(method, path):
        if "cancel" in path:
            return {"data": {"status": "cancel_scheduled"}}
        if "portal-sessions" in path:
            return {"data": {"urls": {"general": {"overview": "https://portal"}}}}
        return {"data": {"id": "s1"}}

    assert await pa.get_subscription("s1") == {"id": "s1"}
    assert await pa.cancel_subscription("s1") == {"status": "cancel_scheduled"}
    assert await pa.create_portal_session("c1") == "https://portal"


@pytest.mark.asyncio
async def test_portal_subscription_fallback_url(monkeypatch):
    async def fake_req(method, path, **kw):
        return {"data": {"urls": {"subscriptions": [
            {"update_subscription_payment_method": "https://sub-url"}]}}}

    monkeypatch.setattr(pa, "_request", fake_req)
    assert await pa.create_portal_session("c1", "s9") == "https://sub-url"


def test_tier_for_price(monkeypatch):
    assert pa.tier_for_price("price_pro") == "pro"
    assert pa.tier_for_price("nope") is None
