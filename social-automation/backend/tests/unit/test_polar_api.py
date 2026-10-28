"""Unit tests for the Polar API client — webhook signature verification,
tier mapping, and configuration gating. No network calls.
"""
import base64
import hashlib
import hmac
import time
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import Settings
from app.services import polar_api


@pytest.fixture
def polar_settings(monkeypatch):
    """Settings instance with Polar fields populated."""
    s = Settings(
        POLAR_ACCESS_TOKEN="polar_oat_test",
        POLAR_WEBHOOK_SECRET="",
        POLAR_ENVIRONMENT="sandbox",
        POLAR_PRODUCT_PRO="prod_pro",
        POLAR_PRODUCT_BUSINESS="prod_biz",
        POLAR_PRODUCT_ENTERPRISE="prod_ent",
        BILLING_PROVIDER="polar",
    )
    monkeypatch.setattr(polar_api, "_settings", lambda: s)
    return s


def _sign(raw: bytes, key: bytes, msg_id: str, ts: int) -> str:
    signed = f"{msg_id}.{ts}.".encode() + raw
    return "v1," + base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()


def test_polar_api_base_sandbox(polar_settings):
    assert polar_settings.polar_api_base == "https://sandbox-api.polar.sh/v1"


def test_polar_api_base_production(polar_settings):
    polar_settings.POLAR_ENVIRONMENT = "production"
    assert polar_settings.polar_api_base == "https://api.polar.sh/v1"


def test_polar_configured_requires_token_and_products(polar_settings):
    assert polar_api.polar_configured() is True
    polar_settings.POLAR_ACCESS_TOKEN = ""
    assert polar_api.polar_configured() is False
    polar_settings.POLAR_ACCESS_TOKEN = "x"
    polar_settings.POLAR_PRODUCT_PRO = ""
    polar_settings.POLAR_PRODUCT_BUSINESS = ""
    polar_settings.POLAR_PRODUCT_ENTERPRISE = ""
    assert polar_api.polar_configured() is False


def test_tier_for_product(polar_settings):
    assert polar_api.tier_for_product("prod_biz") == "business"
    assert polar_api.tier_for_product("unknown") is None


def test_billing_provider_normalization(polar_settings):
    assert polar_settings.billing_provider == "polar"
    polar_settings.BILLING_PROVIDER = " POLAR "
    assert polar_settings.billing_provider == "polar"
    polar_settings.BILLING_PROVIDER = "bogus"
    assert polar_settings.billing_provider == "paddle"


def test_webhook_signature_whsec_base64(polar_settings):
    key = b"test-secret-key-material"
    polar_settings.POLAR_WEBHOOK_SECRET = "whsec_" + base64.b64encode(key).decode()
    raw = b'{"type":"subscription.active","data":{}}'
    ts = int(time.time())
    sig = _sign(raw, key, "msg_1", ts)
    assert polar_api.verify_webhook_signature(raw, "msg_1", str(ts), sig) is True


def test_webhook_signature_polar_prefix(polar_settings):
    key = b"polar-key-material-here"
    polar_settings.POLAR_WEBHOOK_SECRET = "polar_whs_" + base64.b64encode(key).decode()
    raw = b'{"type":"order.paid","data":{"x":1}}'
    ts = int(time.time())
    sig = _sign(raw, key, "msg_2", ts)
    assert polar_api.verify_webhook_signature(raw, "msg_2", str(ts), sig) is True


def test_webhook_signature_raw_secret(polar_settings):
    secret = "plain-string-secret"
    polar_settings.POLAR_WEBHOOK_SECRET = secret
    raw = b"body"
    ts = int(time.time())
    sig = _sign(raw, secret.encode(), "msg_3", ts)
    assert polar_api.verify_webhook_signature(raw, "msg_3", str(ts), sig) is True


def test_webhook_signature_rejects_tampered_body(polar_settings):
    key = b"k"
    polar_settings.POLAR_WEBHOOK_SECRET = "whsec_" + base64.b64encode(key).decode()
    ts = int(time.time())
    sig = _sign(b"original", key, "msg_4", ts)
    assert polar_api.verify_webhook_signature(b"tampered", "msg_4", str(ts), sig) is False


def test_webhook_signature_rejects_stale_timestamp(polar_settings):
    key = b"k"
    polar_settings.POLAR_WEBHOOK_SECRET = "whsec_" + base64.b64encode(key).decode()
    raw = b"body"
    ts = int(time.time()) - polar_api.WEBHOOK_TOLERANCE_S - 10
    sig = _sign(raw, key, "msg_5", ts)
    assert polar_api.verify_webhook_signature(raw, "msg_5", str(ts), sig) is False


def test_webhook_signature_rejects_missing_parts(polar_settings):
    key = b"k"
    polar_settings.POLAR_WEBHOOK_SECRET = "whsec_" + base64.b64encode(key).decode()
    ts = int(time.time())
    sig = _sign(b"body", key, "msg_6", ts)
    assert polar_api.verify_webhook_signature(b"body", "", str(ts), sig) is False
    assert polar_api.verify_webhook_signature(b"body", "msg_6", "", sig) is False
    assert polar_api.verify_webhook_signature(b"body", "msg_6", str(ts), "") is False


def test_webhook_signature_multi_sig_header(polar_settings):
    """webhook-signature may carry several space-separated v1 entries."""
    key = b"k"
    polar_settings.POLAR_WEBHOOK_SECRET = "whsec_" + base64.b64encode(key).decode()
    raw = b"body"
    ts = int(time.time())
    good = _sign(raw, key, "msg_7", ts)
    header = "v1,invalidsig " + good
    assert polar_api.verify_webhook_signature(raw, "msg_7", str(ts), header) is True


def _discount(**over):
    d = {
        "id": "disc_1",
        "name": "Launch",
        "code": "LAUNCH20",
        "type": "percentage",
        "basis_points": 2000,
        "duration": "once",
        "starts_at": None,
        "ends_at": None,
        "max_redemptions": None,
        "redemptions_count": 0,
    }
    d.update(over)
    return d


def _stub_discounts(monkeypatch, items):
    async def fake_request(method, path, **kwargs):
        assert method == "GET" and path == "/discounts/"
        assert kwargs["params"]["query"]
        return {"items": items}

    monkeypatch.setattr(polar_api, "_request", fake_request)


@pytest.mark.asyncio
async def test_get_discount_for_code_matches_case_insensitive(monkeypatch, polar_settings):
    _stub_discounts(monkeypatch, [_discount()])
    d = await polar_api.get_discount_for_code("  launch20 ")
    assert d["id"] == "disc_1"


@pytest.mark.asyncio
async def test_get_discount_for_code_returns_none_without_match(monkeypatch, polar_settings):
    _stub_discounts(monkeypatch, [_discount(code="OTHER10")])
    assert await polar_api.get_discount_for_code("LAUNCH20") is None
    assert await polar_api.get_discount_for_code("") is None


def test_discount_is_redeemable_windows():
    now = datetime.now(UTC)
    future = (now + timedelta(days=1)).isoformat()
    past = (now - timedelta(days=1)).isoformat()
    assert polar_api.discount_is_redeemable(_discount()) is True
    assert polar_api.discount_is_redeemable(_discount(starts_at=future)) is False
    assert polar_api.discount_is_redeemable(_discount(ends_at=past)) is False
    assert polar_api.discount_is_redeemable(
        _discount(max_redemptions=5, redemptions_count=5)
    ) is False
    assert polar_api.discount_is_redeemable(
        _discount(max_redemptions=5, redemptions_count=4)
    ) is True
    # Naive datetimes from Polar are treated as UTC
    assert polar_api.discount_is_redeemable(_discount(ends_at=past[:19])) is False


@pytest.mark.asyncio
async def test_get_discount_id_for_code_only_when_redeemable(monkeypatch, polar_settings):
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    _stub_discounts(monkeypatch, [_discount(ends_at=past)])
    assert await polar_api.get_discount_id_for_code("LAUNCH20") is None

    _stub_discounts(monkeypatch, [_discount()])
    assert await polar_api.get_discount_id_for_code("LAUNCH20") == "disc_1"


class _FakeResp:
    def __init__(self, status=200, body=None, text="", json_exc=None):
        self.status_code = status
        self._body = body
        self.text = text
        self._json_exc = json_exc

    def json(self):
        if self._json_exc:
            raise self._json_exc
        return self._body


class _FakeClient:
    """Stand-in for httpx.AsyncClient — records requests, returns canned resp."""

    last: "_FakeClient | None" = None

    def __init__(self, resp):
        self._resp = resp
        self.calls = []
        _FakeClient.last = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def request(self, method, url, headers=None, **kwargs):
        self.calls.append({"method": method, "url": url, "headers": headers, **kwargs})
        return self._resp


def _patch_httpx(monkeypatch, resp):
    """Route polar_api's httpx.AsyncClient to a canned response."""

    def factory(*a, **kw):
        return _FakeClient(resp)

    monkeypatch.setattr(polar_api.httpx, "AsyncClient", factory)


@pytest.mark.asyncio
async def test_request_success_returns_json_and_headers(monkeypatch, polar_settings):
    _patch_httpx(monkeypatch, _FakeResp(body={"ok": True}))
    out = await polar_api._request("GET", "/things")
    assert out == {"ok": True}
    call = _FakeClient.last.calls[0]
    assert call["url"] == "https://sandbox-api.polar.sh/v1/things"
    assert call["headers"]["Authorization"] == "Bearer polar_oat_test"


@pytest.mark.asyncio
async def test_request_non_json_body_returns_empty(monkeypatch, polar_settings):
    _patch_httpx(monkeypatch, _FakeResp(body=None, json_exc=ValueError("bad")))
    assert await polar_api._request("GET", "/x") == {}


@pytest.mark.asyncio
async def test_request_error_detail_string(monkeypatch, polar_settings):
    _patch_httpx(monkeypatch, _FakeResp(status=404, body={"detail": "not found"}))
    with pytest.raises(polar_api.PolarError, match="404.*not found"):
        await polar_api._request("GET", "/missing")


@pytest.mark.asyncio
async def test_request_error_detail_list_joined(monkeypatch, polar_settings):
    body = {"detail": [{"msg": "bad field"}, {"msg": "worse"}]}
    _patch_httpx(monkeypatch, _FakeResp(status=422, body=body))
    with pytest.raises(polar_api.PolarError, match="bad field.*worse"):
        await polar_api._request("POST", "/x")


@pytest.mark.asyncio
async def test_request_error_falls_back_to_text(monkeypatch, polar_settings):
    _patch_httpx(monkeypatch, _FakeResp(status=500, body={}, text="server exploded"))
    with pytest.raises(polar_api.PolarError, match="server exploded"):
        await polar_api._request("GET", "/x")


def test_webhook_signature_rejects_non_numeric_timestamp(polar_settings):
    key = b"k"
    polar_settings.POLAR_WEBHOOK_SECRET = "whsec_" + base64.b64encode(key).decode()
    sig = _sign(b"body", key, "msg", int(time.time()))
    assert polar_api.verify_webhook_signature(b"body", "msg", "not-a-ts", sig) is False


@pytest.mark.asyncio
async def test_get_or_create_customer_existing(monkeypatch, polar_settings):
    async def fake_request(method, path, **kwargs):
        assert method == "GET" and path == "/customers/external/team-9"
        return {"id": "cust_existing"}

    monkeypatch.setattr(polar_api, "_request", fake_request)
    assert await polar_api.get_or_create_customer("team-9", "a@b.c") == "cust_existing"


@pytest.mark.asyncio
async def test_get_or_create_customer_creates_on_404(monkeypatch, polar_settings):
    calls = []

    async def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if method == "GET":
            raise polar_api.PolarError("Polar GET -> 404: not found")
        return {"id": "cust_new"}

    monkeypatch.setattr(polar_api, "_request", fake_request)
    cid = await polar_api.get_or_create_customer("team-9", "a@b.c", name="Ann")
    assert cid == "cust_new"
    payload = calls[1][2]["json"]
    assert payload["external_id"] == "team-9"
    assert payload["email"] == "a@b.c"
    assert payload["name"] == "Ann"
    assert payload["metadata"]["team_id"] == "team-9"


@pytest.mark.asyncio
async def test_get_or_create_customer_omits_name_when_absent(monkeypatch, polar_settings):
    async def fake_request(method, path, **kwargs):
        if method == "GET":
            raise polar_api.PolarError("404")
        return {"id": "cust_new"}

    monkeypatch.setattr(polar_api, "_request", fake_request)
    await polar_api.get_or_create_customer("team-9", "a@b.c")


@pytest.mark.asyncio
async def test_get_or_create_customer_reraises_non_404(monkeypatch, polar_settings):
    async def fake_request(method, path, **kwargs):
        raise polar_api.PolarError("Polar GET -> 500: boom")

    monkeypatch.setattr(polar_api, "_request", fake_request)
    with pytest.raises(polar_api.PolarError, match="500"):
        await polar_api.get_or_create_customer("team-9", "a@b.c")


@pytest.mark.asyncio
async def test_create_checkout_with_customer_id(monkeypatch, polar_settings):
    captured = {}

    async def fake_request(method, path, **kwargs):
        captured.update(kwargs.get("json") or {})
        return {"id": "chk_1", "url": "https://polar.sh/checkout/chk_1"}

    monkeypatch.setattr(polar_api, "_request", fake_request)
    out = await polar_api.create_checkout(
        product_id="prod_pro", team_id="t1", customer_id="cust_1",
        customer_email="a@b.c", customer_name="Ann", discount_id="disc_9",
    )
    assert out == {"id": "chk_1", "checkout_url": "https://polar.sh/checkout/chk_1"}
    assert captured["customer_id"] == "cust_1"
    assert captured["customer_email"] == "a@b.c"
    assert captured["customer_name"] == "Ann"
    assert captured["discount_id"] == "disc_9"
    assert captured["metadata"]["team_id"] == "t1"
    assert "external_customer_id" not in captured


@pytest.mark.asyncio
async def test_create_checkout_external_customer_when_no_id(monkeypatch, polar_settings):
    captured = {}

    async def fake_request(method, path, **kwargs):
        captured.update(kwargs.get("json") or {})
        return {"id": "chk_2"}

    monkeypatch.setattr(polar_api, "_request", fake_request)
    out = await polar_api.create_checkout(product_id="prod_pro", team_id="t2")
    assert out["checkout_url"] is None
    assert captured["external_customer_id"] == "t2"
    assert "customer_id" not in captured
    assert "discount_id" not in captured


@pytest.mark.asyncio
async def test_get_and_cancel_subscription(monkeypatch, polar_settings):
    calls = []

    async def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"id": "sub_1"}

    monkeypatch.setattr(polar_api, "_request", fake_request)
    assert await polar_api.get_subscription("sub_1") == {"id": "sub_1"}
    assert calls[0][0] == "GET" and calls[0][1] == "/subscriptions/sub_1"
    assert await polar_api.cancel_subscription("sub_1") == {"id": "sub_1"}
    assert calls[1][0] == "PATCH"
    assert calls[1][2]["json"] == {"cancel_at_period_end": True}


@pytest.mark.asyncio
async def test_create_portal_session(monkeypatch, polar_settings):
    captured = {}

    async def fake_request(method, path, **kwargs):
        captured.update(kwargs.get("json") or {})
        return {"customer_portal_url": "https://polar.sh/portal/abc"}

    monkeypatch.setattr(polar_api, "_request", fake_request)
    url = await polar_api.create_portal_session("cust_7")
    assert url == "https://polar.sh/portal/abc"
    assert captured["customer_id"] == "cust_7"


def test_parse_polar_dt_invalid_returns_none():
    assert polar_api._parse_polar_dt("not-a-date") is None
    assert polar_api._parse_polar_dt(None) is None
    naive = polar_api._parse_polar_dt("2026-01-01T00:00:00")
    assert naive.tzinfo == UTC


@pytest.mark.asyncio
async def test_billing_digest_dispatches_to_polar(monkeypatch, polar_settings):
    """build_billing_digest uses the Polar digest when BILLING_PROVIDER=polar."""
    from app.services import paddle_digest

    monkeypatch.setattr(paddle_digest, "get_settings", lambda: polar_settings)

    async def fake_polar_digest():
        return "polar-report"

    import app.services.polar_digest as pd

    monkeypatch.setattr(pd, "build_polar_digest", fake_polar_digest)
    assert await paddle_digest.build_billing_digest() == "polar-report"
