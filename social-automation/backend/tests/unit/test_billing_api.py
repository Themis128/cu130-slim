"""Unit tests for app.api.billing — every route, helper, and webhook handler.

Endpoints are invoked directly with a fake AsyncSession; provider HTTP calls
are monkeypatched. No network, no database.
"""
import base64
import hashlib
import hmac
import json
import time
import uuid
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from app.api import billing
from app.core.config import Settings
from app.models.billing import BillingEvent
from app.models.social_account import SocialAccount
from app.models.user import Team, User
from app.services import dodo_api, paddle_api, polar_api


# ---------------------------------------------------------------------------
# Fixtures / fakes
# ---------------------------------------------------------------------------

def _settings(provider: str, **over) -> Settings:
    kw = {
        "BILLING_PROVIDER": provider,
        "POLAR_ACCESS_TOKEN": "polar_tok",
        "POLAR_WEBHOOK_SECRET": "",
        "POLAR_PRODUCT_PRO": "pp_pro",
        "POLAR_PRODUCT_BUSINESS": "pp_biz",
        "POLAR_PRODUCT_ENTERPRISE": "pp_ent",
        "DODO_PAYMENTS_API_KEY": "dodo_key",
        "DODO_WEBHOOK_SECRET": "",
        "DODO_PRODUCT_PRO": "dp_pro",
        "DODO_PRODUCT_BUSINESS": "dp_biz",
        "DODO_PRODUCT_ENTERPRISE": "dp_ent",
        "PADDLE_API_KEY": "pad_key",
        "PADDLE_CLIENT_TOKEN": "pad_tok",
        "PADDLE_WEBHOOK_SECRET": "",
        "PADDLE_PRICE_PRO": "pri_pro",
        "PADDLE_PRICE_BUSINESS": "pri_biz",
        "PADDLE_PRICE_ENTERPRISE": "pri_ent",
    }
    kw.update(over)
    return Settings(**kw)


@pytest.fixture
def make_env(monkeypatch):
    """Patch billing + all three provider modules to share one Settings obj."""

    def _make(provider: str, **over):
        s = _settings(provider, **over)
        monkeypatch.setattr(billing, "get_settings", lambda: s)
        for mod in (polar_api, dodo_api, paddle_api):
            monkeypatch.setattr(mod, "_settings", lambda s=s: s)
        return s

    return _make


class _Result:
    def __init__(self, value):
        self._v = value

    def scalar_one_or_none(self):
        if isinstance(self._v, list):
            return self._v[0] if self._v else None
        return self._v

    def scalars(self):
        v = self._v if isinstance(self._v, list) else ([self._v] if self._v else [])
        return iter(v)


class FakeDB:
    """AsyncSession stand-in dispatching select() by entity + bind params."""

    def __init__(self, teams=(), users=(), accounts=(), events=None):
        self.teams = list(teams)
        self.users = list(users)
        self.accounts = list(accounts)
        self.events = dict(events or {})
        self.added = []
        self.commits = 0

    async def execute(self, stmt):
        entity = stmt.column_descriptions[0]["entity"]
        params = set(stmt.compile().params.values())
        if entity is Team:
            for t in self.teams:
                keys = {
                    t.id, t.owner_id, t.paddle_customer_id,
                    t.polar_customer_id, t.dodo_customer_id,
                }
                if params & {k for k in keys if k is not None}:
                    return _Result(t)
            return _Result(None)
        if entity is User:
            for u in self.users:
                if u.id in params or u.email in params:
                    return _Result(u)
            return _Result(None)
        if entity is BillingEvent:
            for p in params:
                if p in self.events:
                    return _Result(self.events[p])
            return _Result(None)
        if entity is SocialAccount:
            return _Result(self.accounts)
        return _Result(None)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


class FakeRequest:
    def __init__(self, raw: bytes, headers: dict, payload=None, json_exc=None):
        self._raw = raw
        self.headers = headers
        self._payload = payload
        self._json_exc = json_exc

    async def body(self):
        return self._raw

    async def json(self):
        if self._json_exc:
            raise self._json_exc
        if self._payload is not None:
            return self._payload
        return json.loads(self._raw)


def _owner_team():
    owner = User(id=uuid.uuid4(), email="owner@x.io", name="Owner")
    team = Team(id=uuid.uuid4(), name="t", owner_id=owner.id)
    team.plan_tier = "free"
    team.subscription_status = "none"
    return owner, team


def _polar_req(event, key: bytes, msg_id: str = "msg_1") -> FakeRequest:
    raw = json.dumps(event).encode()
    ts = int(time.time())
    signed = f"{msg_id}.{ts}.".encode() + raw
    sig = "v1," + base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    return FakeRequest(raw, {
        "webhook-id": msg_id,
        "webhook-timestamp": str(ts),
        "webhook-signature": sig,
    })


def _dodo_req(event, key: bytes, msg_id: str = "dmsg_1") -> FakeRequest:
    return _polar_req(event, key, msg_id)


@pytest.fixture
def polar_webhook_settings(make_env):
    key = b"wh-test-key"
    s = make_env("polar", POLAR_WEBHOOK_SECRET="whsec_" + base64.b64encode(key).decode())
    return s, key


@pytest.fixture
def dodo_webhook_settings(make_env):
    key = b"dodo-wh-key"
    s = make_env("dodo", DODO_WEBHOOK_SECRET="whsec_" + base64.b64encode(key).decode())
    return s, key


@pytest.fixture
def sent_emails(monkeypatch):
    sent = []

    async def fake_send(subject=None, text_body=None, to_addrs=None, **kw):
        sent.append({"subject": subject, "to": to_addrs})
        return True

    import app.services.email_digest as ed

    monkeypatch.setattr(ed, "send_email", fake_send)
    return sent


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_parse_dt(self):
        assert billing._parse_dt(None) is None
        assert billing._parse_dt("") is None
        dt = billing._parse_dt("2026-01-15T10:00:00Z")
        assert dt.year == 2026 and dt.tzinfo is not None

    def test_tier_price_id_all_providers(self, make_env):
        make_env("polar")
        assert billing._tier_price_id("pro") == "pp_pro"
        assert billing._tier_price_id("nope") is None
        make_env("dodo")
        assert billing._tier_price_id("business") == "dp_biz"
        make_env("paddle")
        assert billing._tier_price_id("enterprise") == "pri_ent"
        assert billing._tier_price_id("nope") is None

    def test_billing_configured_per_provider(self, make_env):
        make_env("polar")
        assert billing._billing_configured() is True
        make_env("dodo")
        assert billing._billing_configured() is True
        make_env("paddle")
        assert billing._billing_configured() is True

    def test_require_billing_raises_when_unconfigured(self, make_env):
        make_env("polar", POLAR_ACCESS_TOKEN="")
        with pytest.raises(HTTPException) as e:
            billing._require_billing()
        assert e.value.status_code == 503
        assert "polar" in e.value.detail

    @pytest.mark.asyncio
    async def test_get_team_found_and_missing(self, make_env):
        make_env("polar")
        _, team = _owner_team()
        db = FakeDB(teams=[team])
        assert await billing._get_team(team.id, db) is team
        with pytest.raises(HTTPException) as e:
            await billing._get_team(uuid.uuid4(), db)
        assert e.value.status_code == 404

    def test_logsafe_strips_crlf(self):
        assert billing._logsafe("a\r\nb") == "a  b"


# ---------------------------------------------------------------------------
# Simple GET routes
# ---------------------------------------------------------------------------

class TestReadRoutes:
    @pytest.mark.asyncio
    async def test_billing_config_polar(self, make_env):
        s = make_env("polar")
        owner, team = _owner_team()
        out = await billing.billing_config(team.id, FakeDB(), owner)
        assert out["provider"] == "polar"
        assert out["configured"] is True
        assert out["environment"] == s.POLAR_ENVIRONMENT
        assert out["client_token"] is None
        assert out["prices"]["pro"] == "pp_pro"
        assert out["customer_email"] == "owner@x.io"

    @pytest.mark.asyncio
    async def test_billing_config_dodo(self, make_env):
        s = make_env("dodo")
        owner, team = _owner_team()
        out = await billing.billing_config(team.id, FakeDB(), owner)
        assert out["provider"] == "dodo"
        assert out["environment"] == s.DODO_ENVIRONMENT

    @pytest.mark.asyncio
    async def test_billing_config_paddle_includes_client_token(self, make_env):
        make_env("paddle")
        owner, team = _owner_team()
        out = await billing.billing_config(team.id, FakeDB(), owner)
        assert out["provider"] == "paddle"
        assert out["client_token"] == "pad_tok"

    @pytest.mark.asyncio
    async def test_list_plans(self, make_env):
        make_env("polar")
        out = await billing.list_plans(uuid.uuid4(), FakeDB())
        tiers = {p["tier"] for p in out["plans"]}
        assert {"free", "pro", "business", "enterprise"} <= tiers
        pro = next(p for p in out["plans"] if p["tier"] == "pro")
        assert pro["purchasable"] is True and pro["price_id"] == "pp_pro"
        free = next(p for p in out["plans"] if p["tier"] == "free")
        assert free["purchasable"] is False

    @pytest.mark.asyncio
    async def test_get_subscription(self, make_env):
        make_env("polar")
        _, team = _owner_team()
        team.polar_customer_id = "pc_1"
        team.polar_subscription_id = "ps_1"
        team.polar_discount_code = "SAVE10"
        team.dodo_customer_id = "dc_1"
        team.dodo_subscription_id = "ds_1"
        team.paddle_customer_id = "ctm_1"
        team.paddle_subscription_id = "sub_1"
        out = await billing.get_subscription(team.id, FakeDB(teams=[team]))
        assert out["provider"] == "polar"
        assert out["polar_subscription_id"] == "ps_1"
        assert out["polar_discount_code"] == "SAVE10"
        assert out["dodo_subscription_id"] == "ds_1"
        assert out["paddle_subscription_id"] == "sub_1"


# ---------------------------------------------------------------------------
# Discount endpoints (Polar)
# ---------------------------------------------------------------------------

_DISCOUNT = {
    "id": "disc_1", "name": "Launch", "code": "LAUNCH20", "type": "percentage",
    "basis_points": 2000, "amount": None, "currency": "usd", "duration": "once",
    "duration_in_months": None, "starts_at": None, "ends_at": None,
    "max_redemptions": None, "redemptions_count": 0,
}


class TestDiscount:
    def test_discount_payload_none_and_full(self):
        assert billing._discount_payload(None) is None
        out = billing._discount_payload(_DISCOUNT)
        assert out["id"] == "disc_1" and out["basis_points"] == 2000

    @pytest.mark.asyncio
    async def test_resolve_discount_non_polar(self, make_env):
        make_env("dodo")
        assert await billing._resolve_discount("X") == (None, False)

    @pytest.mark.asyncio
    async def test_resolve_discount_empty_code(self, make_env):
        make_env("polar")
        assert await billing._resolve_discount("   ") == (None, False)
        assert await billing._resolve_discount(None) == (None, False)

    @pytest.mark.asyncio
    async def test_resolve_discount_polar_error_nonfatal(self, make_env, monkeypatch):
        make_env("polar")

        async def boom(code):
            raise polar_api.PolarError("down")

        monkeypatch.setattr(polar_api, "get_discount_for_code", boom)
        assert await billing._resolve_discount("X") == (None, False)

    @pytest.mark.asyncio
    async def test_resolve_discount_success(self, make_env, monkeypatch):
        make_env("polar")

        async def fake(code):
            return dict(_DISCOUNT)

        monkeypatch.setattr(polar_api, "get_discount_for_code", fake)
        payload, valid = await billing._resolve_discount("launch20")
        assert valid is True and payload["code"] == "LAUNCH20"

    @pytest.mark.asyncio
    async def test_get_discount_route(self, make_env, monkeypatch):
        make_env("polar")
        _, team = _owner_team()
        team.polar_discount_code = "LAUNCH20"

        async def fake(code):
            return dict(_DISCOUNT)

        monkeypatch.setattr(polar_api, "get_discount_for_code", fake)
        out = await billing.get_discount(team.id, FakeDB(teams=[team]), _owner_team()[0])
        assert out == {
            "provider": "polar", "code": "LAUNCH20",
            "valid": True, "discount": out["discount"],
        }
        assert out["discount"]["id"] == "disc_1"

    @pytest.mark.asyncio
    async def test_set_discount_normalizes_and_stores(self, make_env, monkeypatch):
        make_env("polar")
        owner, team = _owner_team()

        async def fake(code):
            return dict(_DISCOUNT)

        monkeypatch.setattr(polar_api, "get_discount_for_code", fake)
        db = FakeDB(teams=[team])
        out = await billing.set_discount(
            billing.DiscountCodeRequest(code="  launch20 "), team.id, db, owner,
        )
        assert team.polar_discount_code == "LAUNCH20"
        assert db.commits == 1
        assert out["valid"] is True

    @pytest.mark.asyncio
    async def test_set_discount_empty_clears(self, make_env):
        make_env("polar")
        owner, team = _owner_team()
        team.polar_discount_code = "OLD"
        out = await billing.set_discount(
            billing.DiscountCodeRequest(code=""), team.id, FakeDB(teams=[team]), owner,
        )
        assert team.polar_discount_code is None
        assert out["code"] is None and out["valid"] is False

    @pytest.mark.asyncio
    async def test_clear_discount(self, make_env):
        make_env("polar")
        owner, team = _owner_team()
        team.polar_discount_code = "OLD"
        out = await billing.clear_discount(team.id, FakeDB(teams=[team]), owner)
        assert team.polar_discount_code is None
        assert out == {"provider": "polar", "code": None, "valid": False, "discount": None}


# ---------------------------------------------------------------------------
# POST /checkout
# ---------------------------------------------------------------------------

class TestCheckout:
    @pytest.mark.asyncio
    async def test_unconfigured_503(self, make_env):
        make_env("polar", POLAR_ACCESS_TOKEN="")
        owner, team = _owner_team()
        with pytest.raises(HTTPException) as e:
            await billing.create_checkout(
                billing.CheckoutRequest(tier="pro"), team.id, FakeDB(), owner,
            )
        assert e.value.status_code == 503

    @pytest.mark.asyncio
    async def test_invalid_tier_400(self, make_env):
        make_env("polar")
        owner, team = _owner_team()
        with pytest.raises(HTTPException) as e:
            await billing.create_checkout(
                billing.CheckoutRequest(tier="free"), team.id, FakeDB(), owner,
            )
        assert e.value.status_code == 400

    @pytest.mark.asyncio
    async def test_no_price_400(self, make_env):
        make_env("polar", POLAR_PRODUCT_BUSINESS="")
        owner, team = _owner_team()
        with pytest.raises(HTTPException) as e:
            await billing.create_checkout(
                billing.CheckoutRequest(tier="business"), team.id, FakeDB(teams=[team]), owner,
            )
        assert e.value.status_code == 400
        assert "No price" in e.value.detail

    @pytest.mark.asyncio
    async def test_polar_checkout_with_team_discount_code(self, make_env, monkeypatch):
        make_env("polar")
        owner, team = _owner_team()
        team.polar_discount_code = "SAVE10"
        captured = {}

        async def fake_discount(code):
            return "disc_42"

        async def fake_checkout(**kw):
            captured.update(kw)
            return {"id": "chk_1", "checkout_url": "https://polar/co/1"}

        monkeypatch.setattr(polar_api, "get_discount_id_for_code", fake_discount)
        monkeypatch.setattr(polar_api, "create_checkout", fake_checkout)
        out = await billing.create_checkout(
            billing.CheckoutRequest(tier="pro"), team.id, FakeDB(teams=[team]), owner,
        )
        assert out.transaction_id == "chk_1"
        assert captured["discount_id"] == "disc_42"
        assert captured["product_id"] == "pp_pro"

    @pytest.mark.asyncio
    async def test_polar_checkout_request_code_overrides_team(self, make_env, monkeypatch):
        make_env("polar")
        owner, team = _owner_team()
        team.polar_discount_code = "TEAMCODE"
        seen = []

        async def fake_discount(code):
            seen.append(code)
            return None

        async def fake_checkout(**kw):
            return {"id": "chk", "checkout_url": "u"}

        monkeypatch.setattr(polar_api, "get_discount_id_for_code", fake_discount)
        monkeypatch.setattr(polar_api, "create_checkout", fake_checkout)
        await billing.create_checkout(
            billing.CheckoutRequest(tier="pro", discount_code="OVERRIDE"),
            team.id, FakeDB(teams=[team]), owner,
        )
        assert seen == ["OVERRIDE"]

    @pytest.mark.asyncio
    async def test_polar_checkout_discount_lookup_failure_nonfatal(self, make_env, monkeypatch):
        make_env("polar")
        owner, team = _owner_team()
        team.polar_discount_code = "X"
        captured = {}

        async def boom(code):
            raise polar_api.PolarError("api down")

        async def fake_checkout(**kw):
            captured.update(kw)
            return {"id": "chk", "checkout_url": "u"}

        monkeypatch.setattr(polar_api, "get_discount_id_for_code", boom)
        monkeypatch.setattr(polar_api, "create_checkout", fake_checkout)
        out = await billing.create_checkout(
            billing.CheckoutRequest(tier="pro"), team.id, FakeDB(teams=[team]), owner,
        )
        assert out.transaction_id == "chk"
        assert captured["discount_id"] is None

    @pytest.mark.asyncio
    async def test_polar_checkout_no_code_skips_lookup(self, make_env, monkeypatch):
        make_env("polar")
        owner, team = _owner_team()
        called = []

        async def fake_checkout(**kw):
            return {"id": "chk", "checkout_url": "u"}

        monkeypatch.setattr(polar_api, "get_discount_id_for_code",
                            lambda c: called.append(c) or None)
        monkeypatch.setattr(polar_api, "create_checkout", fake_checkout)
        await billing.create_checkout(
            billing.CheckoutRequest(tier="pro"), team.id, FakeDB(teams=[team]), owner,
        )
        assert called == []

    @pytest.mark.asyncio
    async def test_polar_checkout_already_active_409(self, make_env, monkeypatch):
        make_env("polar")
        owner, team = _owner_team()

        async def boom(**kw):
            raise polar_api.PolarError("AlreadyActiveSubscription: customer has sub")

        monkeypatch.setattr(polar_api, "create_checkout", boom)
        with pytest.raises(HTTPException) as e:
            await billing.create_checkout(
                billing.CheckoutRequest(tier="pro"), team.id, FakeDB(teams=[team]), owner,
            )
        assert e.value.status_code == 409

    @pytest.mark.asyncio
    async def test_polar_checkout_other_error_502(self, make_env, monkeypatch):
        make_env("polar")
        owner, team = _owner_team()

        async def boom(**kw):
            raise polar_api.PolarError("Polar POST /checkouts -> 500")

        monkeypatch.setattr(polar_api, "create_checkout", boom)
        with pytest.raises(HTTPException) as e:
            await billing.create_checkout(
                billing.CheckoutRequest(tier="pro"), team.id, FakeDB(teams=[team]), owner,
            )
        assert e.value.status_code == 502

    @pytest.mark.asyncio
    async def test_dodo_checkout_success_and_error(self, make_env, monkeypatch):
        make_env("dodo")
        owner, team = _owner_team()
        captured = {}

        async def fake_checkout(**kw):
            captured.update(kw)
            return {"id": "sess_1", "checkout_url": "https://dodo/co/1"}

        monkeypatch.setattr(dodo_api, "create_checkout", fake_checkout)
        out = await billing.create_checkout(
            billing.CheckoutRequest(tier="business"), team.id, FakeDB(teams=[team]), owner,
        )
        assert out.checkout_url == "https://dodo/co/1"
        assert captured["product_id"] == "dp_biz"

        async def boom(**kw):
            raise dodo_api.DodoError("Dodo POST /checkouts -> 400")

        monkeypatch.setattr(dodo_api, "create_checkout", boom)
        with pytest.raises(HTTPException) as e:
            await billing.create_checkout(
                billing.CheckoutRequest(tier="business"), team.id, FakeDB(teams=[team]), owner,
            )
        assert e.value.status_code == 502

    @pytest.mark.asyncio
    async def test_paddle_checkout_existing_customer(self, make_env, monkeypatch):
        make_env("paddle")
        owner, team = _owner_team()
        team.paddle_customer_id = "ctm_1"
        captured = {}

        async def fake_txn(**kw):
            captured.update(kw)
            return {"id": "txn_1", "checkout_url": "https://paddle/co"}

        monkeypatch.setattr(paddle_api, "create_checkout_transaction", fake_txn)
        out = await billing.create_checkout(
            billing.CheckoutRequest(tier="pro"), team.id, FakeDB(teams=[team]), owner,
        )
        assert out.transaction_id == "txn_1"
        assert captured["customer_id"] == "ctm_1"

    @pytest.mark.asyncio
    async def test_paddle_checkout_creates_customer(self, make_env, monkeypatch):
        make_env("paddle")
        owner, team = _owner_team()
        db = FakeDB(teams=[team])

        async def fake_customer(tid, email, name):
            return "ctm_new"

        async def fake_txn(**kw):
            return {"id": "txn_2", "checkout_url": "u"}

        monkeypatch.setattr(paddle_api, "get_or_create_customer", fake_customer)
        monkeypatch.setattr(paddle_api, "create_checkout_transaction", fake_txn)
        out = await billing.create_checkout(
            billing.CheckoutRequest(tier="pro"), team.id, db, owner,
        )
        assert out.transaction_id == "txn_2"
        assert team.paddle_customer_id == "ctm_new"
        assert db.commits >= 1

    @pytest.mark.asyncio
    async def test_paddle_checkout_error_502(self, make_env, monkeypatch):
        make_env("paddle")
        owner, team = _owner_team()

        async def boom(*a, **kw):
            raise paddle_api.PaddleError("nope")

        monkeypatch.setattr(paddle_api, "get_or_create_customer", boom)
        with pytest.raises(HTTPException) as e:
            await billing.create_checkout(
                billing.CheckoutRequest(tier="pro"), team.id, FakeDB(teams=[team]), owner,
            )
        assert e.value.status_code == 502


# ---------------------------------------------------------------------------
# POST /portal
# ---------------------------------------------------------------------------

class TestPortal:
    @pytest.mark.asyncio
    async def test_polar_portal_creates_customer(self, make_env, monkeypatch):
        make_env("polar")
        owner, team = _owner_team()
        db = FakeDB(teams=[team])

        async def fake_customer(tid, email, name):
            return "pc_new"

        async def fake_session(cid):
            return "https://polar/portal/x"

        monkeypatch.setattr(polar_api, "get_or_create_customer", fake_customer)
        monkeypatch.setattr(polar_api, "create_portal_session", fake_session)
        out = await billing.customer_portal(team.id, db, owner)
        assert out == {"portal_url": "https://polar/portal/x"}
        assert team.polar_customer_id == "pc_new"
        assert db.commits >= 1

    @pytest.mark.asyncio
    async def test_polar_portal_existing_customer(self, make_env, monkeypatch):
        make_env("polar")
        owner, team = _owner_team()
        team.polar_customer_id = "pc_1"

        async def fake_session(cid):
            assert cid == "pc_1"
            return "url"

        monkeypatch.setattr(polar_api, "create_portal_session", fake_session)
        out = await billing.customer_portal(team.id, FakeDB(teams=[team]), owner)
        assert out["portal_url"] == "url"

    @pytest.mark.asyncio
    async def test_polar_portal_error_502(self, make_env, monkeypatch):
        make_env("polar")
        owner, team = _owner_team()
        team.polar_customer_id = "pc_1"

        async def boom(cid):
            raise polar_api.PolarError("down")

        monkeypatch.setattr(polar_api, "create_portal_session", boom)
        with pytest.raises(HTTPException) as e:
            await billing.customer_portal(team.id, FakeDB(teams=[team]), owner)
        assert e.value.status_code == 502

    @pytest.mark.asyncio
    async def test_dodo_portal_creates_customer(self, make_env, monkeypatch):
        make_env("dodo")
        owner, team = _owner_team()
        db = FakeDB(teams=[team])

        async def fake_customer(tid, email, name):
            return "dc_new"

        async def fake_session(cid):
            return "https://dodo/portal"

        monkeypatch.setattr(dodo_api, "get_or_create_customer", fake_customer)
        monkeypatch.setattr(dodo_api, "create_portal_session", fake_session)
        out = await billing.customer_portal(team.id, db, owner)
        assert out["portal_url"] == "https://dodo/portal"
        assert team.dodo_customer_id == "dc_new"

    @pytest.mark.asyncio
    async def test_dodo_portal_existing_and_error(self, make_env, monkeypatch):
        make_env("dodo")
        owner, team = _owner_team()
        team.dodo_customer_id = "dc_1"

        async def fake_session(cid):
            return "u"

        monkeypatch.setattr(dodo_api, "create_portal_session", fake_session)
        out = await billing.customer_portal(team.id, FakeDB(teams=[team]), owner)
        assert out["portal_url"] == "u"

        async def boom(cid):
            raise dodo_api.DodoError("x")

        monkeypatch.setattr(dodo_api, "create_portal_session", boom)
        with pytest.raises(HTTPException) as e:
            await billing.customer_portal(team.id, FakeDB(teams=[team]), owner)
        assert e.value.status_code == 502

    @pytest.mark.asyncio
    async def test_paddle_portal_requires_customer(self, make_env):
        make_env("paddle")
        owner, team = _owner_team()
        with pytest.raises(HTTPException) as e:
            await billing.customer_portal(team.id, FakeDB(teams=[team]), owner)
        assert e.value.status_code == 400

    @pytest.mark.asyncio
    async def test_paddle_portal_success_and_error(self, make_env, monkeypatch):
        make_env("paddle")
        owner, team = _owner_team()
        team.paddle_customer_id = "ctm_1"
        team.paddle_subscription_id = "sub_1"

        async def fake_session(cid, sid):
            assert cid == "ctm_1" and sid == "sub_1"
            return "https://paddle/portal"

        monkeypatch.setattr(paddle_api, "create_portal_session", fake_session)
        out = await billing.customer_portal(team.id, FakeDB(teams=[team]), owner)
        assert out["portal_url"] == "https://paddle/portal"

        async def boom(cid, sid):
            raise paddle_api.PaddleError("x")

        monkeypatch.setattr(paddle_api, "create_portal_session", boom)
        with pytest.raises(HTTPException) as e:
            await billing.customer_portal(team.id, FakeDB(teams=[team]), owner)
        assert e.value.status_code == 502


# ---------------------------------------------------------------------------
# POST /cancel and /sync
# ---------------------------------------------------------------------------

class TestCancelSync:
    @pytest.mark.asyncio
    async def test_polar_cancel_requires_sub(self, make_env):
        make_env("polar")
        owner, team = _owner_team()
        with pytest.raises(HTTPException) as e:
            await billing.cancel_subscription(team.id, FakeDB(teams=[team]), owner)
        assert e.value.status_code == 400

    @pytest.mark.asyncio
    async def test_polar_cancel_success(self, make_env, monkeypatch):
        make_env("polar")
        owner, team = _owner_team()
        team.polar_subscription_id = "ps_1"

        async def fake_cancel(sid):
            return {"current_period_end": "2026-11-01T00:00:00Z"}

        monkeypatch.setattr(polar_api, "cancel_subscription", fake_cancel)
        db = FakeDB(teams=[team])
        out = await billing.cancel_subscription(team.id, db, owner)
        assert out["status"] == "canceled_pending"
        assert team.subscription_status == "canceled_pending"
        assert out["period_end"].month == 11

    @pytest.mark.asyncio
    async def test_polar_cancel_no_end_and_error(self, make_env, monkeypatch):
        make_env("polar")
        owner, team = _owner_team()
        team.polar_subscription_id = "ps_1"

        async def fake_cancel(sid):
            return {}

        monkeypatch.setattr(polar_api, "cancel_subscription", fake_cancel)
        out = await billing.cancel_subscription(team.id, FakeDB(teams=[team]), owner)
        assert out["period_end"] is None

        async def boom(sid):
            raise polar_api.PolarError("x")

        monkeypatch.setattr(polar_api, "cancel_subscription", boom)
        with pytest.raises(HTTPException) as e:
            await billing.cancel_subscription(team.id, FakeDB(teams=[team]), owner)
        assert e.value.status_code == 502

    @pytest.mark.asyncio
    async def test_dodo_cancel(self, make_env, monkeypatch):
        make_env("dodo")
        owner, team = _owner_team()

        with pytest.raises(HTTPException) as e:
            await billing.cancel_subscription(team.id, FakeDB(teams=[team]), owner)
        assert e.value.status_code == 400

        team.dodo_subscription_id = "ds_1"

        async def fake_cancel(sid):
            return {"next_billing_date": "2026-12-01T00:00:00Z"}

        monkeypatch.setattr(dodo_api, "cancel_subscription", fake_cancel)
        out = await billing.cancel_subscription(team.id, FakeDB(teams=[team]), owner)
        assert out["status"] == "canceled_pending"
        assert out["period_end"].month == 12

        async def boom(sid):
            raise dodo_api.DodoError("x")

        monkeypatch.setattr(dodo_api, "cancel_subscription", boom)
        with pytest.raises(HTTPException) as e:
            await billing.cancel_subscription(team.id, FakeDB(teams=[team]), owner)
        assert e.value.status_code == 502

    @pytest.mark.asyncio
    async def test_paddle_cancel(self, make_env, monkeypatch):
        make_env("paddle")
        owner, team = _owner_team()

        with pytest.raises(HTTPException) as e:
            await billing.cancel_subscription(team.id, FakeDB(teams=[team]), owner)
        assert e.value.status_code == 400

        team.paddle_subscription_id = "sub_1"

        async def fake_cancel(sid):
            return {"current_billing_period": {"ends_at": "2026-10-15T00:00:00Z"}}

        monkeypatch.setattr(paddle_api, "cancel_subscription", fake_cancel)
        out = await billing.cancel_subscription(team.id, FakeDB(teams=[team]), owner)
        assert out["status"] == "canceled_pending"

        async def fake_cancel2(sid):
            return {"current_billing_period": None}

        monkeypatch.setattr(paddle_api, "cancel_subscription", fake_cancel2)
        out = await billing.cancel_subscription(team.id, FakeDB(teams=[team]), owner)
        assert out["period_end"].month == 10  # unchanged from previous call

        async def boom(sid):
            raise paddle_api.PaddleError("x")

        monkeypatch.setattr(paddle_api, "cancel_subscription", boom)
        with pytest.raises(HTTPException) as e:
            await billing.cancel_subscription(team.id, FakeDB(teams=[team]), owner)
        assert e.value.status_code == 502

    @pytest.mark.asyncio
    async def test_polar_sync(self, make_env, monkeypatch):
        make_env("polar")
        owner, team = _owner_team()
        with pytest.raises(HTTPException) as e:
            await billing.sync_subscription(team.id, FakeDB(teams=[team]), owner)
        assert e.value.status_code == 400

        team.polar_subscription_id = "ps_1"

        async def fake_sub(sid):
            return {"id": "ps_1", "status": "active", "product_id": "pp_biz"}

        monkeypatch.setattr(polar_api, "get_subscription", fake_sub)
        out = await billing.sync_subscription(team.id, FakeDB(teams=[team]), owner)
        assert out["plan_tier"] == "business"

        async def boom(sid):
            raise polar_api.PolarError("x")

        monkeypatch.setattr(polar_api, "get_subscription", boom)
        with pytest.raises(HTTPException) as e:
            await billing.sync_subscription(team.id, FakeDB(teams=[team]), owner)
        assert e.value.status_code == 502

    @pytest.mark.asyncio
    async def test_dodo_sync(self, make_env, monkeypatch):
        make_env("dodo")
        owner, team = _owner_team()
        with pytest.raises(HTTPException) as e:
            await billing.sync_subscription(team.id, FakeDB(teams=[team]), owner)
        assert e.value.status_code == 400

        team.dodo_subscription_id = "ds_1"

        async def fake_sub(sid):
            return {"subscription_id": "ds_1", "status": "active", "product_id": "dp_pro"}

        monkeypatch.setattr(dodo_api, "get_subscription", fake_sub)
        out = await billing.sync_subscription(team.id, FakeDB(teams=[team]), owner)
        assert out["plan_tier"] == "pro"

        async def boom(sid):
            raise dodo_api.DodoError("x")

        monkeypatch.setattr(dodo_api, "get_subscription", boom)
        with pytest.raises(HTTPException) as e:
            await billing.sync_subscription(team.id, FakeDB(teams=[team]), owner)
        assert e.value.status_code == 502

    @pytest.mark.asyncio
    async def test_paddle_sync(self, make_env, monkeypatch):
        make_env("paddle")
        owner, team = _owner_team()
        with pytest.raises(HTTPException) as e:
            await billing.sync_subscription(team.id, FakeDB(teams=[team]), owner)
        assert e.value.status_code == 400

        team.paddle_subscription_id = "sub_1"

        async def fake_sub(sid):
            return {"id": "sub_1", "status": "active",
                    "items": [{"price": {"id": "pri_biz"}}]}

        monkeypatch.setattr(paddle_api, "get_subscription", fake_sub)
        out = await billing.sync_subscription(team.id, FakeDB(teams=[team]), owner)
        assert out["plan_tier"] == "business"

        async def boom(sid):
            raise paddle_api.PaddleError("x")

        monkeypatch.setattr(paddle_api, "get_subscription", boom)
        with pytest.raises(HTTPException) as e:
            await billing.sync_subscription(team.id, FakeDB(teams=[team]), owner)
        assert e.value.status_code == 502


# ---------------------------------------------------------------------------
# _apply_* mappers
# ---------------------------------------------------------------------------

def _team(**kw):
    t = Team(id=uuid.uuid4(), name="t", owner_id=uuid.uuid4())
    t.plan_tier = "free"
    t.subscription_status = "none"
    for k, v in kw.items():
        setattr(t, k, v)
    return t


class TestApplySubscription:
    def test_paddle_apply_basic(self):
        t = _team(paddle_subscription_id="sub_old")
        billing._apply_subscription(t, {
            "id": "sub_new", "status": "active",
            "current_billing_period": {"ends_at": "2026-11-01T00:00:00Z"},
            "items": [{"price": {"id": "pri_pro"}}],
        })
        assert t.paddle_subscription_id == "sub_new"
        assert t.subscription_status == "active"
        assert t.subscription_period_end.month == 11

    def test_paddle_apply_no_items_and_canceled(self):
        t = _team(plan_tier="pro", subscription_status="active")
        billing._apply_subscription(t, {"status": "canceled", "items": []})
        assert t.plan_tier == "free"

    def test_paddle_apply_keeps_tier_when_status_inactive(self, make_env):
        make_env("paddle")
        t = _team(plan_tier="pro", subscription_status="past_due")
        billing._apply_subscription(t, {
            "status": "incomplete",
            "items": [{"price": {"id": "pri_biz"}}],
        })
        assert t.plan_tier == "pro"  # incomplete not in active statuses

    def test_paddle_apply_tier_upgrade(self, make_env):
        make_env("paddle")
        t = _team()
        billing._apply_subscription(t, {
            "status": "trialing",
            "items": [{"price": {"id": "pri_ent"}}],
        })
        assert t.plan_tier == "enterprise"

    def test_polar_apply_cancel_at_period_end(self, make_env):
        make_env("polar")
        t = _team(subscription_status="active")
        billing._apply_polar_subscription(t, {
            "id": "ps_1", "status": "active", "cancel_at_period_end": True,
            "customer_id": "pc_1", "current_period_end": "2026-11-01T00:00:00Z",
            "product_id": "pp_pro",
        })
        assert t.subscription_status == "canceled_pending"
        assert t.polar_customer_id == "pc_1"
        assert t.plan_tier == "pro"

    def test_polar_apply_nested_customer_and_product(self, make_env):
        make_env("polar")
        t = _team()
        billing._apply_polar_subscription(t, {
            "id": "ps_2", "status": "active",
            "customer": {"id": "pc_nest"}, "ends_at": "2026-12-01T00:00:00Z",
            "product": {"id": "pp_biz"},
        })
        assert t.polar_customer_id == "pc_nest"
        assert t.plan_tier == "business"
        assert t.subscription_period_end.month == 12

    def test_polar_apply_ended_goes_free(self, make_env):
        make_env("polar")
        t = _team(plan_tier="pro", subscription_status="active")
        billing._apply_polar_subscription(t, {"status": "unpaid"})
        assert t.plan_tier == "free"
        t2 = _team(plan_tier="pro")
        billing._apply_polar_subscription(t2, {"status": "active", "ended_at": "2026-01-01"})
        assert t2.plan_tier == "free"

    def test_dodo_apply_variants(self, make_env):
        make_env("dodo")
        t = _team(subscription_status="active")
        billing._apply_dodo_subscription(t, {
            "subscription_id": "ds_1", "status": "active",
            "cancel_at_next_billing_date": True,
            "customer": {"customer_id": "dc_1"},
            "next_billing_date": "2026-11-01T00:00:00Z",
            "product_id": "dp_pro",
        })
        assert t.dodo_subscription_id == "ds_1"
        assert t.subscription_status == "canceled_pending"
        assert t.dodo_customer_id == "dc_1"
        assert t.plan_tier == "pro"

        t2 = _team(plan_tier="pro")
        billing._apply_dodo_subscription(t2, {
            "id": "ds_2", "status": "cancelled", "customer_id": "dc_2",
        })
        assert t2.plan_tier == "free"
        assert t2.dodo_customer_id == "dc_2"


# ---------------------------------------------------------------------------
# _sync_dm_auto_reply + _notify_team_owner
# ---------------------------------------------------------------------------

class TestDmSyncAndNotify:
    @pytest.mark.asyncio
    async def test_sync_dm_none_db_and_skip_branches(self):
        t = _team()
        await billing._sync_dm_auto_reply(None, t, paid=True)  # early return

        accounts = [
            SocialAccount(platform="youtube", account_type="person", meta_data={}),
            SocialAccount(
                platform="twitter", account_type="person",
                meta_data={"twitter_auto_reply": {"enabled": True}},
            ),
        ]

        class _Result:
            def scalars(self):
                return iter(accounts)

        class _Db:
            async def execute(self, _s):
                return _Result()

        await billing._sync_dm_auto_reply(_Db(), t, paid=True)
        assert "youtube_auto_reply" not in str(accounts[0].meta_data)
        assert accounts[1].meta_data["twitter_auto_reply"]["enabled"] is True

    @pytest.mark.asyncio
    async def test_notify_owner_no_user(self):
        db = FakeDB(users=[])
        t = _team()
        await billing._notify_team_owner(db, t, "s", "b", "tpl")  # returns quietly

    @pytest.mark.asyncio
    async def test_notify_owner_sends(self, sent_emails):
        owner, team = _owner_team()
        db = FakeDB(users=[owner])
        await billing._notify_team_owner(db, team, "Subj", "Body", "tpl")
        assert sent_emails == [{"subject": "Subj", "to": ["owner@x.io"]}]

    @pytest.mark.asyncio
    async def test_notify_owner_email_failure_swallowed(self, monkeypatch):
        owner, team = _owner_team()
        db = FakeDB(users=[owner])

        async def boom(**kw):
            raise RuntimeError("smtp down")

        import app.services.email_digest as ed

        monkeypatch.setattr(ed, "send_email", boom)
        await billing._notify_team_owner(db, team, "s", "b", "tpl")  # no raise


# ---------------------------------------------------------------------------
# Team resolution helpers
# ---------------------------------------------------------------------------

class TestTeamResolution:
    @pytest.mark.asyncio
    async def test_paddle_find_team_by_custom_data(self):
        _, team = _owner_team()
        db = FakeDB(teams=[team])
        found = await billing._find_team_for_event(
            db, {"custom_data": {"team_id": str(team.id)}},
        )
        assert found is team

    @pytest.mark.asyncio
    async def test_paddle_find_team_invalid_id_falls_to_customer(self):
        team = _team(paddle_customer_id="ctm_9")
        db = FakeDB(teams=[team])
        found = await billing._find_team_for_event(
            db, {"custom_data": {"team_id": "not-a-uuid"}, "customer_id": "ctm_9"},
        )
        assert found is team

    @pytest.mark.asyncio
    async def test_paddle_find_team_none(self):
        db = FakeDB()
        assert await billing._find_team_for_event(db, {}) is None
        assert await billing._find_team_for_event(
            db, {"custom_data": {"team_id": str(uuid.uuid4())}},
        ) is None

    @pytest.mark.asyncio
    async def test_polar_find_team_candidates(self):
        _, team = _owner_team()
        db = FakeDB(teams=[team])
        # external_customer_id path
        assert await billing._find_team_for_polar_event(
            db, {"external_customer_id": str(team.id)},
        ) is team
        # customer.external_id path
        assert await billing._find_team_for_polar_event(
            db, {"customer": {"external_id": str(team.id)}},
        ) is team
        # invalid candidate skipped, customer_id fallback
        team2 = _team(polar_customer_id="pc_9")
        db2 = FakeDB(teams=[team2])
        found = await billing._find_team_for_polar_event(
            db2, {"metadata": {"team_id": "bad"}, "customer_id": "pc_9"},
        )
        assert found is team2
        # nothing resolves
        assert await billing._find_team_for_polar_event(FakeDB(), {"data": 1}) is None

    @pytest.mark.asyncio
    async def test_dodo_find_team_paths(self):
        _, team = _owner_team()
        db = FakeDB(teams=[team])
        assert await billing._find_team_for_dodo_event(
            db, {"metadata": {"team_id": str(team.id)}},
        ) is team

        team2 = _team(dodo_customer_id="dc_9")
        db2 = FakeDB(teams=[team2])
        assert await billing._find_team_for_dodo_event(
            db2, {"metadata": {"team_id": "bad"}, "customer_id": "dc_9"},
        ) is team2
        assert await billing._find_team_for_dodo_event(
            db2, {"customer": {"customer_id": "dc_9"}},
        ) is team2

        # email → user → owned team
        owner, team3 = _owner_team()
        db3 = FakeDB(teams=[team3], users=[owner])
        found = await billing._find_team_for_dodo_event(
            db3, {"customer": {"email": "owner@x.io"}},
        )
        assert found is team3

        # email matching no user → None
        assert await billing._find_team_for_dodo_event(
            db3, {"customer": {"email": "ghost@x.io"}},
        ) is None
        assert await billing._find_team_for_dodo_event(FakeDB(), {}) is None


# ---------------------------------------------------------------------------
# Paddle webhook + subscription events
# ---------------------------------------------------------------------------

class TestPaddleWebhook:
    @pytest.mark.asyncio
    async def test_bad_signature_401(self, make_env, monkeypatch):
        make_env("paddle")
        monkeypatch.setattr(paddle_api, "verify_webhook_signature", lambda r, s: False)
        req = FakeRequest(b"{}", {"Paddle-Signature": "bad"})
        with pytest.raises(HTTPException) as e:
            await billing.paddle_webhook(req, FakeDB())
        assert e.value.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_json_400(self, make_env, monkeypatch):
        make_env("paddle")
        monkeypatch.setattr(paddle_api, "verify_webhook_signature", lambda r, s: True)
        req = FakeRequest(b"not json", {"Paddle-Signature": "ok"}, json_exc=ValueError())
        with pytest.raises(HTTPException) as e:
            await billing.paddle_webhook(req, FakeDB())
        assert e.value.status_code == 400

    @pytest.mark.asyncio
    async def test_duplicate_event(self, make_env, monkeypatch):
        make_env("paddle")
        monkeypatch.setattr(paddle_api, "verify_webhook_signature", lambda r, s: True)
        ev = BillingEvent(event_id="e_dup", event_type="subscription.updated", payload={})
        db = FakeDB(events={"e_dup": ev})
        req = FakeRequest(b"{}", {"Paddle-Signature": "ok"},
                          payload={"event_id": "e_dup", "event_type": "subscription.updated", "data": {}})
        out = await billing.paddle_webhook(req, db)
        assert out == {"status": "duplicate", "event_id": "e_dup"}

    @pytest.mark.asyncio
    async def test_subscription_event_processed(self, make_env, monkeypatch, sent_emails):
        make_env("paddle")
        monkeypatch.setattr(paddle_api, "verify_webhook_signature", lambda r, s: True)
        owner, team = _owner_team()
        db = FakeDB(teams=[team], users=[owner])
        event = {
            "event_id": "e1", "event_type": "subscription.activated",
            "data": {
                "custom_data": {"team_id": str(team.id)},
                "id": "sub_1", "status": "active",
                "items": [{"price": {"id": "pri_pro"}}],
            },
        }
        req = FakeRequest(json.dumps(event).encode(), {"Paddle-Signature": "ok"}, payload=event)
        out = await billing.paddle_webhook(req, db)
        assert out == {"status": "processed", "event_type": "subscription.activated"}
        assert team.plan_tier == "pro"
        assert team.paddle_subscription_id == "sub_1"
        row = next(r for r in db.added if isinstance(r, BillingEvent))
        assert row.team_id == team.id
        assert sent_emails  # activation email sent

    @pytest.mark.asyncio
    async def test_transaction_completed_links_subscription(self, make_env, monkeypatch):
        make_env("paddle")
        monkeypatch.setattr(paddle_api, "verify_webhook_signature", lambda r, s: True)
        _, team = _owner_team()
        db = FakeDB(teams=[team])
        event = {
            "event_id": "e2", "event_type": "transaction.completed",
            "data": {"custom_data": {"team_id": str(team.id)}, "subscription_id": "sub_77"},
        }
        req = FakeRequest(b"{}", {"Paddle-Signature": "ok"}, payload=event)
        out = await billing.paddle_webhook(req, db)
        assert out["status"] == "processed"
        assert team.paddle_subscription_id == "sub_77"

    @pytest.mark.asyncio
    async def test_event_without_team_and_other_types(self, make_env, monkeypatch):
        make_env("paddle")
        monkeypatch.setattr(paddle_api, "verify_webhook_signature", lambda r, s: True)
        db = FakeDB()
        for et in ("subscription.created", "transaction.paid", "adjustment.created"):
            event = {"event_id": f"e_{et}", "event_type": et, "data": {}}
            req = FakeRequest(b"{}", {"Paddle-Signature": "ok"}, payload=event)
            out = await billing.paddle_webhook(req, db)
            assert out["status"] == "processed"

    @pytest.mark.asyncio
    async def test_handler_error_500_and_row_error(self, make_env, monkeypatch):
        make_env("paddle")
        monkeypatch.setattr(paddle_api, "verify_webhook_signature", lambda r, s: True)

        async def boom(db, team, et, data):
            raise RuntimeError("explode")

        monkeypatch.setattr(billing, "_handle_subscription_event", boom)
        _, team = _owner_team()
        db = FakeDB(teams=[team])
        event = {
            "event_id": "e_err", "event_type": "subscription.updated",
            "data": {"custom_data": {"team_id": str(team.id)}},
        }
        req = FakeRequest(b"{}", {"Paddle-Signature": "ok"}, payload=event)
        with pytest.raises(HTTPException) as e:
            await billing.paddle_webhook(req, db)
        assert e.value.status_code == 500
        row = next(r for r in db.added if isinstance(r, BillingEvent))
        assert "explode" in row.error

    @pytest.mark.asyncio
    async def test_handle_subscription_event_branches(self, make_env, sent_emails):
        make_env("paddle")
        db = FakeDB()

        # no team → warn + return
        await billing._handle_subscription_event(db, None, "subscription.updated", {})

        def _db_for(team):
            owner = User(id=team.owner_id, email="o@x.io")
            return FakeDB(users=[owner])

        # customer_id stored + canceled path
        team = _team(plan_tier="pro")
        await billing._handle_subscription_event(
            _db_for(team), team, "subscription.canceled",
            {"customer_id": "ctm_5", "status": "canceled"},
        )
        assert team.paddle_customer_id == "ctm_5"
        assert team.plan_tier == "free"
        assert sent_emails[-1]["subject"] == "SocialAuto subscription ended"

        # past_due
        team2 = _team()
        await billing._handle_subscription_event(
            _db_for(team2), team2, "subscription.past_due", {},
        )
        assert team2.subscription_status == "past_due"
        assert sent_emails[-1]["subject"] == "SocialAuto payment failed"

        # paused + resumed + updated + expired
        team3 = _team()
        await billing._handle_subscription_event(FakeDB(), team3, "subscription.paused", {})
        assert team3.subscription_status == "paused"
        await billing._handle_subscription_event(
            FakeDB(), team3, "subscription.resumed",
            {"status": "active", "items": [{"price": {"id": "pri_pro"}}]},
        )
        assert team3.subscription_status == "active"
        await billing._handle_subscription_event(
            FakeDB(), team3, "subscription.updated", {"status": "active"},
        )
        await billing._handle_subscription_event(
            FakeDB(), team3, "subscription.expired", {"status": "expired"},
        )
        assert team3.plan_tier == "free"


# ---------------------------------------------------------------------------
# Polar webhook + subscription events
# ---------------------------------------------------------------------------

class TestPolarWebhook:
    @pytest.mark.asyncio
    async def test_bad_signature_401(self, polar_webhook_settings):
        req = FakeRequest(b"{}", {
            "webhook-id": "m", "webhook-timestamp": "1", "webhook-signature": "v1,bad",
        })
        with pytest.raises(HTTPException) as e:
            await billing.polar_webhook(req, FakeDB())
        assert e.value.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_json_400(self, polar_webhook_settings):
        _, key = polar_webhook_settings
        raw = b"definitely not json"
        ts = int(time.time())
        signed = f"m1.{ts}.".encode() + raw
        sig = "v1," + base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
        req = FakeRequest(raw, {
            "webhook-id": "m1", "webhook-timestamp": str(ts), "webhook-signature": sig,
        }, json_exc=ValueError())
        with pytest.raises(HTTPException) as e:
            await billing.polar_webhook(req, FakeDB())
        assert e.value.status_code == 400

    @pytest.mark.asyncio
    async def test_duplicate(self, polar_webhook_settings):
        _, key = polar_webhook_settings
        ev = BillingEvent(event_id="m_dup", event_type="subscription.active", payload={})
        req = _polar_req({"type": "subscription.active", "data": {}}, key, "m_dup")
        out = await billing.polar_webhook(req, FakeDB(events={"m_dup": ev}))
        assert out == {"status": "duplicate", "event_id": "m_dup"}

    @pytest.mark.asyncio
    async def test_subscription_active_end_to_end(self, polar_webhook_settings, sent_emails):
        _, key = polar_webhook_settings
        owner, team = _owner_team()
        db = FakeDB(teams=[team], users=[owner])
        event = {
            "type": "subscription.active",
            "data": {
                "id": "ps_1", "status": "active",
                "metadata": {"team_id": str(team.id)},
                "customer_id": "pc_1", "product_id": "pp_biz",
            },
        }
        out = await billing.polar_webhook(_polar_req(event, key), db)
        assert out["status"] == "processed"
        assert team.plan_tier == "business"
        assert team.polar_subscription_id == "ps_1"
        assert team.polar_customer_id == "pc_1"
        assert sent_emails

    @pytest.mark.asyncio
    async def test_order_paid_links_subscription(self, polar_webhook_settings):
        _, key = polar_webhook_settings
        _, team = _owner_team()
        db = FakeDB(teams=[team])
        event = {
            "type": "order.paid",
            "data": {
                "metadata": {"team_id": str(team.id)},
                "subscription_id": "ps_9",
            },
        }
        out = await billing.polar_webhook(_polar_req(event, key, "m_op"), db)
        assert out["status"] == "processed"
        assert team.polar_subscription_id == "ps_9"

    @pytest.mark.asyncio
    async def test_event_without_team(self, polar_webhook_settings):
        _, key = polar_webhook_settings
        event = {"type": "subscription.updated", "data": {"id": "x"}}
        out = await billing.polar_webhook(_polar_req(event, key, "m_nt"), FakeDB())
        assert out["status"] == "processed"

    @pytest.mark.asyncio
    async def test_handler_error_500(self, polar_webhook_settings, monkeypatch):
        _, key = polar_webhook_settings

        async def boom(db, team, et, data):
            raise RuntimeError("kaput")

        monkeypatch.setattr(billing, "_handle_polar_subscription_event", boom)
        _, team = _owner_team()
        db = FakeDB(teams=[team])
        event = {
            "type": "subscription.canceled",
            "data": {"id": "ps_1", "metadata": {"team_id": str(team.id)}},
        }
        req = _polar_req(event, key, "m_err")
        with pytest.raises(HTTPException) as e:
            await billing.polar_webhook(req, db)
        assert e.value.status_code == 500
        row = next(r for r in db.added if isinstance(r, BillingEvent))
        assert "kaput" in row.error

    @pytest.mark.asyncio
    async def test_handle_polar_event_branches(self, make_env, sent_emails):
        make_env("polar")
        db = FakeDB()
        await billing._handle_polar_subscription_event(db, None, "subscription.active", {})

        team = _team(plan_tier="pro", subscription_status="active",
                     polar_subscription_id="ps_A")
        team2 = _team(plan_tier="free", polar_subscription_id="ps_C")
        team3 = _team()
        db = FakeDB(users=[User(id=t.owner_id, email="o@x.io")
                           for t in (team, team2, team3)])

        # stale sub id, non-activating → ignored
        await billing._handle_polar_subscription_event(
            db, team, "subscription.canceled", {"id": "ps_B", "status": "canceled"},
        )
        assert team.plan_tier == "pro"

        # activating event for a different sub → applies (upgrade path)
        await billing._handle_polar_subscription_event(
            db, team, "subscription.active",
            {"id": "ps_B", "status": "active", "product_id": "pp_ent",
             "customer": {"id": "pc_new"}},
        )
        assert team.polar_subscription_id == "ps_B"
        assert team.plan_tier == "enterprise"
        assert team.polar_customer_id == "pc_new"
        assert sent_emails

        # subscription.updated with active status counts as activating
        await billing._handle_polar_subscription_event(
            db, team2, "subscription.updated",
            {"id": "ps_D", "status": "trialing", "product_id": "pp_pro"},
        )
        assert team2.polar_subscription_id == "ps_D"
        assert team2.plan_tier == "pro"

        # remaining event types
        await billing._handle_polar_subscription_event(
            db, team2, "subscription.past_due", {"id": "ps_D"},
        )
        assert team2.subscription_status == "past_due"
        assert sent_emails[-1]["subject"] == "SocialAuto payment failed"

        await billing._handle_polar_subscription_event(
            db, team2, "subscription.revoked", {"id": "ps_D", "status": "revoked"},
        )
        assert team2.plan_tier == "free"

        await billing._handle_polar_subscription_event(
            db, team2, "subscription.paused", {"id": "ps_D"},
        )
        assert team2.subscription_status == "paused"

        # no stored sub → no stale guard; resumed/uncanceled/created apply
        for et in ("subscription.created", "subscription.resumed",
                   "subscription.uncanceled"):
            await billing._handle_polar_subscription_event(
                db, team3, et, {"status": "active", "product_id": "pp_pro"},
            )
        assert team3.plan_tier == "pro"


# ---------------------------------------------------------------------------
# Dodo webhook + subscription events
# ---------------------------------------------------------------------------

class TestDodoWebhook:
    @pytest.mark.asyncio
    async def test_bad_signature_401(self, dodo_webhook_settings):
        req = FakeRequest(b"{}", {
            "webhook-id": "m", "webhook-timestamp": "1", "webhook-signature": "v1,bad",
        })
        with pytest.raises(HTTPException) as e:
            await billing.dodo_webhook(req, FakeDB())
        assert e.value.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_json_400(self, dodo_webhook_settings):
        _, key = dodo_webhook_settings
        raw = b"nope"
        ts = int(time.time())
        signed = f"d1.{ts}.".encode() + raw
        sig = "v1," + base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
        req = FakeRequest(raw, {
            "webhook-id": "d1", "webhook-timestamp": str(ts), "webhook-signature": sig,
        }, json_exc=ValueError())
        with pytest.raises(HTTPException) as e:
            await billing.dodo_webhook(req, FakeDB())
        assert e.value.status_code == 400

    @pytest.mark.asyncio
    async def test_duplicate(self, dodo_webhook_settings):
        _, key = dodo_webhook_settings
        ev = BillingEvent(event_id="d_dup", event_type="x", payload={})
        req = _dodo_req({"type": "subscription.active", "data": {}}, key, "d_dup")
        out = await billing.dodo_webhook(req, FakeDB(events={"d_dup": ev}))
        assert out["status"] == "duplicate"

    @pytest.mark.asyncio
    async def test_subscription_active_end_to_end(self, dodo_webhook_settings, sent_emails):
        _, key = dodo_webhook_settings
        owner, team = _owner_team()
        db = FakeDB(teams=[team], users=[owner])
        event = {
            "type": "subscription.active",
            "data": {
                "subscription_id": "ds_1", "status": "active",
                "metadata": {"team_id": str(team.id)},
                "customer_id": "dc_1", "product_id": "dp_pro",
            },
        }
        out = await billing.dodo_webhook(_dodo_req(event, key), db)
        assert out["status"] == "processed"
        assert team.plan_tier == "pro"
        assert team.dodo_subscription_id == "ds_1"
        assert sent_emails

    @pytest.mark.asyncio
    async def test_payment_succeeded_links(self, dodo_webhook_settings):
        _, key = dodo_webhook_settings
        _, team = _owner_team()
        db = FakeDB(teams=[team])
        event = {
            "type": "payment.succeeded",
            "data": {
                "metadata": {"team_id": str(team.id)},
                "subscription_id": "ds_5", "customer_id": "dc_5",
            },
        }
        out = await billing.dodo_webhook(_dodo_req(event, key, "d_pay"), db)
        assert out["status"] == "processed"
        assert team.dodo_subscription_id == "ds_5"
        assert team.dodo_customer_id == "dc_5"

    @pytest.mark.asyncio
    async def test_event_without_team(self, dodo_webhook_settings):
        _, key = dodo_webhook_settings
        event = {"type": "subscription.updated", "data": {"subscription_id": "x"}}
        out = await billing.dodo_webhook(_dodo_req(event, key, "d_nt"), FakeDB())
        assert out["status"] == "processed"

    @pytest.mark.asyncio
    async def test_handler_error_500(self, dodo_webhook_settings, monkeypatch):
        _, key = dodo_webhook_settings

        async def boom(db, team, et, data):
            raise RuntimeError("dodo-boom")

        monkeypatch.setattr(billing, "_handle_dodo_subscription_event", boom)
        _, team = _owner_team()
        db = FakeDB(teams=[team])
        event = {
            "type": "subscription.cancelled",
            "data": {"subscription_id": "ds_1", "metadata": {"team_id": str(team.id)}},
        }
        req = _dodo_req(event, key, "d_err")
        with pytest.raises(HTTPException) as e:
            await billing.dodo_webhook(req, db)
        assert e.value.status_code == 500
        row = next(r for r in db.added if isinstance(r, BillingEvent))
        assert "dodo-boom" in row.error

    @pytest.mark.asyncio
    async def test_handle_dodo_event_branches(self, make_env, sent_emails):
        make_env("dodo")
        db = FakeDB()
        await billing._handle_dodo_subscription_event(db, None, "subscription.active", {})

        # stale non-activating ignored; nested customer id stored when applied
        team = _team(plan_tier="pro", subscription_status="active",
                     dodo_subscription_id="ds_A")
        await billing._handle_dodo_subscription_event(
            db, team, "subscription.updated",
            {"subscription_id": "ds_B", "status": "cancelled"},
        )
        assert team.plan_tier == "pro"

        await billing._handle_dodo_subscription_event(
            db, team, "subscription.plan_changed",
            {"subscription_id": "ds_B", "status": "active",
             "product_id": "dp_biz", "customer": {"customer_id": "dc_n"}},
        )
        assert team.dodo_subscription_id == "ds_B"
        assert team.plan_tier == "business"
        assert team.dodo_customer_id == "dc_n"

        # subscription.updated w/ active status → activating for unknown sub
        team2 = _team(dodo_subscription_id="ds_C")
        await billing._handle_dodo_subscription_event(
            db, team2, "subscription.updated",
            {"subscription_id": "ds_D", "status": "active", "product_id": "dp_pro"},
        )
        assert team2.dodo_subscription_id == "ds_D"

        # on_hold + past_due notification
        await billing._handle_dodo_subscription_event(
            db, team2, "subscription.on_hold", {"subscription_id": "ds_D"},
        )
        assert team2.subscription_status == "on_hold"
        await billing._handle_dodo_subscription_event(
            db, team2, "subscription.past_due", {"subscription_id": "ds_D"},
        )
        assert team2.subscription_status == "past_due"
        assert sent_emails[-1]["subject"] == "SocialAuto payment failed"

        # cancelled + expired notify
        await billing._handle_dodo_subscription_event(
            db, team2, "subscription.cancelled",
            {"subscription_id": "ds_D", "status": "cancelled"},
        )
        assert team2.plan_tier == "free"
        assert sent_emails[-1]["subject"] == "SocialAuto subscription ended"

        # failed + paused + renewed/unpaused/active-apply coverage
        team3 = _team()
        await billing._handle_dodo_subscription_event(
            db, team3, "subscription.failed",
            {"subscription_id": "ds_E", "status": "failed"},
        )
        assert team3.subscription_status == "failed"
        await billing._handle_dodo_subscription_event(
            db, team3, "subscription.paused", {"subscription_id": "ds_E"},
        )
        assert team3.subscription_status == "paused"
        for et in ("subscription.renewed", "subscription.unpaused"):
            await billing._handle_dodo_subscription_event(
                db, team3, et,
                {"subscription_id": "ds_E", "status": "active", "product_id": "dp_pro"},
            )
        assert team3.plan_tier == "pro"

        # active → notify
        owner, team4 = _owner_team()
        db4 = FakeDB(users=[owner])
        await billing._handle_dodo_subscription_event(
            db4, team4, "subscription.active",
            {"subscription_id": "ds_F", "status": "active", "product_id": "dp_pro"},
        )
        assert sent_emails[-1]["subject"].endswith("subscription active")
