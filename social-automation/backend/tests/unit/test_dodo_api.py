"""Unit tests for the Dodo Payments client — webhook signature verification,
tier mapping, and configuration gating. No network calls.
"""
import base64
import hashlib
import hmac
import time

import pytest

from app.core.config import Settings
from app.services import dodo_api


@pytest.fixture
def dodo_settings(monkeypatch):
    s = Settings(
        DODO_PAYMENTS_API_KEY="dodo_test_key",
        DODO_WEBHOOK_SECRET="",
        DODO_ENVIRONMENT="test_mode",
        DODO_PRODUCT_PRO="pdt_pro",
        DODO_PRODUCT_BUSINESS="pdt_biz",
        DODO_PRODUCT_ENTERPRISE="pdt_ent",
        BILLING_PROVIDER="dodo",
    )
    monkeypatch.setattr(dodo_api, "_settings", lambda: s)
    return s


def _sign(raw: bytes, key: bytes, msg_id: str, ts: int) -> str:
    signed = f"{msg_id}.{ts}.".encode() + raw
    return "v1," + base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()


def test_dodo_api_base_test_mode(dodo_settings):
    assert dodo_settings.dodo_api_base == "https://test.dodopayments.com"


def test_dodo_api_base_live_mode(dodo_settings):
    dodo_settings.DODO_ENVIRONMENT = "live_mode"
    assert dodo_settings.dodo_api_base == "https://live.dodopayments.com"


def test_dodo_configured_requires_key_and_products(dodo_settings):
    assert dodo_api.dodo_configured() is True
    dodo_settings.DODO_PAYMENTS_API_KEY = ""
    assert dodo_api.dodo_configured() is False


def test_tier_for_product(dodo_settings):
    assert dodo_api.tier_for_product("pdt_ent") == "enterprise"
    assert dodo_api.tier_for_product("unknown") is None


def test_billing_provider_accepts_dodo(dodo_settings):
    assert dodo_settings.billing_provider == "dodo"


def test_webhook_signature_valid(dodo_settings):
    key = b"dodo-secret-material"
    dodo_settings.DODO_WEBHOOK_SECRET = "whsec_" + base64.b64encode(key).decode()
    raw = b'{"type":"subscription.active","data":{}}'
    ts = int(time.time())
    sig = _sign(raw, key, "msg_1", ts)
    assert dodo_api.verify_webhook_signature(raw, "msg_1", str(ts), sig) is True


def test_webhook_signature_rejects_tampered(dodo_settings):
    key = b"k"
    dodo_settings.DODO_WEBHOOK_SECRET = "whsec_" + base64.b64encode(key).decode()
    ts = int(time.time())
    sig = _sign(b"original", key, "msg_2", ts)
    assert dodo_api.verify_webhook_signature(b"tampered", "msg_2", str(ts), sig) is False


def test_webhook_signature_rejects_stale(dodo_settings):
    key = b"k"
    dodo_settings.DODO_WEBHOOK_SECRET = "whsec_" + base64.b64encode(key).decode()
    ts = int(time.time()) - dodo_api.WEBHOOK_TOLERANCE_S - 10
    sig = _sign(b"body", key, "msg_3", ts)
    assert dodo_api.verify_webhook_signature(b"body", "msg_3", str(ts), sig) is False


def test_webhook_signature_rejects_missing(dodo_settings):
    key = b"k"
    dodo_settings.DODO_WEBHOOK_SECRET = "whsec_" + base64.b64encode(key).decode()
    ts = int(time.time())
    sig = _sign(b"body", key, "msg_4", ts)
    assert dodo_api.verify_webhook_signature(b"body", "", str(ts), sig) is False
    assert dodo_api.verify_webhook_signature(b"body", "msg_4", str(ts), "") is False


@pytest.mark.asyncio
async def test_billing_digest_dispatches_to_dodo(monkeypatch, dodo_settings):
    from app.services import paddle_digest

    monkeypatch.setattr(paddle_digest, "get_settings", lambda: dodo_settings)

    async def fake_dodo_digest():
        return "dodo-report"

    import app.services.dodo_digest as dd

    monkeypatch.setattr(dd, "build_dodo_digest", fake_dodo_digest)
    assert await paddle_digest.build_billing_digest() == "dodo-report"
