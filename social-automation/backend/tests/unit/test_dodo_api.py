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
async def test_dodo_event_for_other_subscription_does_not_downgrade():
    """A failed/cancelled event for a *different* subscription must not clobber
    the team's active plan — e.g. a second checkout attempt that fails."""
    from types import SimpleNamespace

    from app.api.billing import _handle_dodo_subscription_event

    team = SimpleNamespace(
        dodo_subscription_id="sub_current",
        dodo_customer_id="cus_1",
        subscription_status="active",
        plan_tier="pro",
    )

    await _handle_dodo_subscription_event(
        None, team, "subscription.failed",
        {"subscription_id": "sub_other", "status": "failed"},
    )
    assert team.subscription_status == "active"
    assert team.plan_tier == "pro"
    assert team.dodo_subscription_id == "sub_current"

    await _handle_dodo_subscription_event(
        None, team, "subscription.cancelled",
        {"subscription_id": "sub_other", "status": "cancelled"},
    )
    assert team.subscription_status == "active"
    assert team.plan_tier == "pro"


@pytest.mark.asyncio
async def test_dodo_event_for_current_subscription_applies():
    from types import SimpleNamespace

    from app.api.billing import _handle_dodo_subscription_event

    team = SimpleNamespace(
        dodo_subscription_id="sub_current",
        dodo_customer_id="cus_1",
        subscription_status="active",
        plan_tier="pro",
    )
    await _handle_dodo_subscription_event(
        None, team, "subscription.failed",
        {"subscription_id": "sub_current", "status": "failed"},
    )
    assert team.subscription_status == "failed"


@pytest.mark.asyncio
async def test_sync_dm_auto_reply_enables_on_paid():
    """Paying for a plan auto-enables DM auto-reply on the team's accounts."""
    from types import SimpleNamespace

    from app.api.billing import _sync_dm_auto_reply
    from app.models.social_account import SocialAccount

    accounts = [
        SocialAccount(
            platform="twitter", account_type="person",
            meta_data={"twitter_auto_reply": {"enabled": False}},
        ),
        SocialAccount(
            platform="facebook", account_type="user",
            meta_data={"personal_messenger_auto_reply": {"enabled": False}},
        ),
        SocialAccount(
            platform="facebook", account_type="page",
            meta_data={"messenger_auto_reply": {"enabled": False}},
        ),
        SocialAccount(platform="linkedin", account_type="person", meta_data={}),
    ]

    class _Result:
        def scalars(self):
            return accounts

    class _Db:
        async def execute(self, _stmt):
            return _Result()

    team = SimpleNamespace(id="team-1")
    await _sync_dm_auto_reply(_Db(), team, paid=True)

    assert accounts[0].meta_data["twitter_auto_reply"]["enabled"] is True
    assert accounts[1].meta_data["personal_messenger_auto_reply"]["enabled"] is True
    assert accounts[2].meta_data["messenger_auto_reply"]["enabled"] is True
    assert accounts[3].meta_data["linkedin_auto_reply"]["enabled"] is True


@pytest.mark.asyncio
async def test_sync_dm_auto_reply_disables_on_free():
    """Downgrading to free disables DM auto-reply across the team's accounts."""
    from types import SimpleNamespace

    from app.api.billing import _sync_dm_auto_reply
    from app.models.social_account import SocialAccount

    accounts = [
        SocialAccount(
            platform="twitter", account_type="person",
            meta_data={"twitter_auto_reply": {"enabled": True}},
        ),
        SocialAccount(
            platform="telegram", account_type="bot",
            meta_data={"telegram_auto_reply": {"enabled": True}},
        ),
    ]

    class _Result:
        def scalars(self):
            return accounts

    class _Db:
        async def execute(self, _stmt):
            return _Result()

    team = SimpleNamespace(id="team-1")
    await _sync_dm_auto_reply(_Db(), team, paid=False)

    assert accounts[0].meta_data["twitter_auto_reply"]["enabled"] is False
    assert accounts[1].meta_data["telegram_auto_reply"]["enabled"] is False


def test_dm_auto_reply_is_paid_feature():
    from app.core.quotas import plan_has_feature

    assert plan_has_feature("free", "dm_auto_reply") is False
    for tier in ("pro", "business", "enterprise"):
        assert plan_has_feature(tier, "dm_auto_reply") is True


@pytest.mark.asyncio
async def test_live_payments_enabled_false_on_merchant_not_live(monkeypatch, dodo_settings):
    """MERCHANT_NOT_LIVE 403 -> False (review still pending)."""

    async def fake_request(method, path, **kwargs):
        raise dodo_api.DodoError(
            "Dodo POST /checkouts -> 403: Live payments not enabled for merchant"
        )

    monkeypatch.setattr(dodo_api, "_request", fake_request)
    assert await dodo_api.live_payments_enabled() is False


@pytest.mark.asyncio
async def test_live_payments_enabled_true_on_success(monkeypatch, dodo_settings):
    """A successful response means the gate is gone (impossible with a fake
    product id, but covered for completeness)."""

    async def fake_request(method, path, **kwargs):
        return {"checkout_url": "https://example.com"}

    monkeypatch.setattr(dodo_api, "_request", fake_request)
    assert await dodo_api.live_payments_enabled() is True


@pytest.mark.asyncio
async def test_live_payments_enabled_true_on_validation_error(monkeypatch, dodo_settings):
    """Once the gate lifts, the invalid probe product yields a 422-style
    DodoError — any non-MERCHANT_NOT_LIVE error means live is enabled."""

    async def fake_request(method, path, **kwargs):
        raise dodo_api.DodoError(
            "Dodo POST /checkouts -> 422: product_id invalid"
        )

    monkeypatch.setattr(dodo_api, "_request", fake_request)
    assert await dodo_api.live_payments_enabled() is True


@pytest.mark.asyncio
async def test_billing_digest_dispatches_to_dodo(monkeypatch, dodo_settings):
    from app.services import paddle_digest

    monkeypatch.setattr(paddle_digest, "get_settings", lambda: dodo_settings)

    async def fake_dodo_digest():
        return "dodo-report"

    import app.services.dodo_digest as dd

    monkeypatch.setattr(dd, "build_dodo_digest", fake_dodo_digest)
    assert await paddle_digest.build_billing_digest() == "dodo-report"
