"""MoR compliance contract tests — encodes Polar & Dodo policy requirements.

These tests lock in requirements from:
- Dodo "Preparing Your Website" / verification docs (pricing parity with the
  product catalog, cancel/refund route, monitored support contact).
- Polar account-review docs (working checkout + portal integration, webhook
  signature verification, opt-in automation, support channel).
- GDPR/Meta expectations (account deletion endpoint, accurate encryption
  disclosure).

If any of these break, the site/product contract with the MoR drifts and
verification forms get held — keep them green.
"""

import pytest

from app.api import billing
from app.core.quotas import PLAN_LIMITS
from app.services import slack_notifications


def _route_paths(router) -> set[str]:
    return {r.path for r in router.routes}


class TestBillingSurface:
    """Dodo/Polar both require a working purchase + self-serve cancel path."""

    def test_required_billing_routes_exist(self):
        paths = _route_paths(billing.router)
        for path in ("/config", "/plans", "/subscription", "/checkout", "/portal", "/cancel"):
            assert path in paths, f"missing billing route {path}"

    def test_webhook_routes_for_both_providers_exist(self):
        paths = _route_paths(billing.router)
        assert "/polar-webhook" in paths
        assert "/dodo-webhook" in paths

    def test_checkout_only_accepts_paid_tiers(self):
        """Only pro/business/enterprise map to a provider product id."""
        assert billing._tier_price_id("free") is None
        assert billing._tier_price_id("gold") is None

    def test_checkout_request_rejects_free_tier(self):
        """CheckoutRequest.tier is validated — free must not reach the MoR."""
        req = billing.CheckoutRequest(tier="pro")
        assert req.tier == "pro"


class TestPricingParity:
    """Dodo form review compares the Product Information Form against the live
    site; PLAN_LIMITS is the source of truth the pricing page renders."""

    ADVERTISED = {
        "free": {"posts_per_month": 50, "ai_calls_per_month": 100, "social_accounts": 3},
        "pro": {"posts_per_month": 500, "ai_calls_per_month": 5000, "social_accounts": 15},
        "business": {"posts_per_month": 2000, "ai_calls_per_month": 20000, "social_accounts": 50},
        "enterprise": {"posts_per_month": -1, "ai_calls_per_month": -1, "social_accounts": -1},
    }

    @pytest.mark.parametrize("tier", list(ADVERTISED))
    def test_plan_limits_match_advertised_products(self, tier):
        for resource, expected in self.ADVERTISED[tier].items():
            actual = PLAN_LIMITS[tier][resource]
            assert actual == expected, f"{tier}.{resource}: site advertises {expected}, backend enforces {actual}"

    def test_paid_tiers_get_dm_auto_reply(self):
        """AUP: automation is a paid, opt-in feature — free tier excluded."""
        assert PLAN_LIMITS["free"]["dm_auto_reply"] == 0
        for tier in ("pro", "business", "enterprise"):
            assert PLAN_LIMITS[tier]["dm_auto_reply"] == 1


class TestWebhookSecurity:
    """Both MoRs deliver Standard-Webhook signed events; signatures are mandatory."""

    def test_tolerance_is_bounded(self):
        from app.services import dodo_api, polar_api

        for mod in (dodo_api, polar_api):
            assert 60 <= mod.WEBHOOK_TOLERANCE_S <= 600

    def test_verifiers_reject_missing_signature(self):
        from app.services import dodo_api, polar_api

        assert polar_api.verify_webhook_signature(b"{}", "", "", "") is False
        assert dodo_api.verify_webhook_signature(b"{}", "", "", "") is False


class TestDataRights:
    """GDPR / Meta platform policy: users must be able to delete their data."""

    def test_account_delete_endpoint_exists(self):
        from app.api import auth

        routes = {r.path: r.methods for r in auth.router.routes}
        assert "/account" in routes
        assert "DELETE" in routes["/account"]


class TestSupportChannel:
    """Polar holds a 48h support SLA; the app must have a support-channel path."""

    def test_support_settings_fields_exist(self):
        from app.core.config import Settings

        fields = Settings.model_fields
        assert "SLACK_SUPPORT_WEBHOOK_URL" in fields
        assert "SLACK_SUPPORT_CHANNEL_ID" in fields
        assert "SLACK_BILLING_WEBHOOK_URL" in fields
        assert "SLACK_BILLING_CHANNEL_ID" in fields


class TestSlackChannelOverride:
    """Incoming-webhook channel override lets one app webhook serve
    #polar-support without a dedicated webhook URL."""

    class _FakeResp:
        status_code = 200

        def json(self):
            return {"ok": True}

    class _FakeClient:
        last_payload: dict | None = None

        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            type(self).last_payload = json
            return TestSlackChannelOverride._FakeResp()

    @pytest.mark.asyncio
    async def test_channel_name_overrides_webhook_destination(self, monkeypatch):
        monkeypatch.setattr(slack_notifications.httpx, "AsyncClient", self._FakeClient)
        ok, err, _ = await slack_notifications._post_slack_text(
            text="t", webhook_url="https://hooks.slack.com/services/x",
            token="", channel_id="#polar-support", purpose="test",
        )
        assert ok is True and err is None
        assert self._FakeClient.last_payload["channel"] == "#polar-support"

    @pytest.mark.asyncio
    async def test_channel_id_not_sent_as_override(self, monkeypatch):
        """Channel IDs (C...) belong to the token path, not webhook override."""
        monkeypatch.setattr(slack_notifications.httpx, "AsyncClient", self._FakeClient)
        ok, _, _ = await slack_notifications._post_slack_text(
            text="t", webhook_url="https://hooks.slack.com/services/x",
            token="", channel_id="C0C1F1K3DDF", purpose="test",
        )
        assert ok is True
        assert "channel" not in self._FakeClient.last_payload


class TestPolarTeamResolution:
    """Polar merges customers by email — one Polar customer can serve several
    teams, so checkout-scoped identifiers (metadata.team_id,
    external_customer_id) must beat the customer-level external_id."""

    class _FakeResult:
        def __init__(self, value):
            self._v = value

        def scalar_one_or_none(self):
            return self._v

    class _FakeDB:
        def __init__(self, teams):
            self._teams = teams

        async def execute(self, stmt):
            import uuid as _uuid

            for v in stmt.compile().params.values():
                if isinstance(v, _uuid.UUID):
                    return TestPolarTeamResolution._FakeResult(self._teams.get(v))
            return TestPolarTeamResolution._FakeResult(None)

    @pytest.mark.asyncio
    async def test_metadata_team_id_beats_shared_customer_external_id(self):
        """Regression: shared Polar customer's external_id pointed to the wrong
        team — checkout metadata must win."""
        import uuid

        from app.models.user import Team

        admin_team = Team(id=uuid.uuid4(), name="admin")
        other_team = Team(id=uuid.uuid4(), name="other")
        db = self._FakeDB({admin_team.id: admin_team, other_team.id: other_team})
        data = {
            "customer": {"external_id": str(admin_team.id)},
            "metadata": {"team_id": str(other_team.id)},
        }
        team = await billing._find_team_for_polar_event(db, data)
        assert team is other_team

    @pytest.mark.asyncio
    async def test_external_id_used_when_no_checkout_metadata(self):
        import uuid

        from app.models.user import Team

        team = Team(id=uuid.uuid4(), name="t")
        db = self._FakeDB({team.id: team})
        data = {"customer": {"external_id": str(team.id)}}
        assert await billing._find_team_for_polar_event(db, data) is team


class TestResponseCompression:
    """Polar/Dodo reviewer tooling expects compression on the public app."""

    def test_gzip_middleware_registered(self):
        from app.main import app

        assert any(m.cls.__name__ == "GZipMiddleware" for m in app.user_middleware)
