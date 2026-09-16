"""Unit tests for the Polar API client — webhook signature verification,
tier mapping, and configuration gating. No network calls.
"""
import base64
import hashlib
import hmac
import time

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
