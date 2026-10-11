"""Coverage for app/services/paddle_digest.py."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.paddle_digest as PD


def _set(monkeypatch, **kw):
    s = SimpleNamespace(
        PADDLE_API_KEY="key", PADDLE_ENVIRONMENT="production",
        billing_provider="paddle", paddle_price_tiers={})
    for k, v in kw.items():
        setattr(s, k, v)
    monkeypatch.setattr(PD, "get_settings", lambda: s)
    return s


class TestMoney:
    def test_none(self):
        assert PD._money(None) == "N/A"

    def test_cents(self):
        assert PD._money(12345) == "USD 123.45"
        assert PD._money(1000, "EUR") == "EUR 10.00"
        assert PD._money(0) == "USD 0.00"


class TestListPaddle:
    @pytest.mark.asyncio
    async def test_no_api_key(self, monkeypatch):
        _set(monkeypatch, PADDLE_API_KEY="")
        assert await PD._list_paddle("/customers") == []

    @pytest.mark.asyncio
    async def test_paddle_error(self, monkeypatch):
        _set(monkeypatch)
        monkeypatch.setattr(
            PD, "_request",
            AsyncMock(side_effect=PD.PaddleError("boom")))
        assert await PD._list_paddle("/customers") == []

    @pytest.mark.asyncio
    async def test_success(self, monkeypatch):
        _set(monkeypatch)
        req = AsyncMock(return_value={"data": [{"id": "c1"}]})
        monkeypatch.setattr(PD, "_request", req)
        out = await PD._list_paddle("/customers", {"status": "active"},
                                    per_page=50)
        assert out == [{"id": "c1"}]
        assert req.await_args.kwargs["params"] == {
            "per_page": "50", "status": "active"}

    @pytest.mark.asyncio
    async def test_empty_data(self, monkeypatch):
        _set(monkeypatch)
        monkeypatch.setattr(PD, "_request", AsyncMock(return_value={}))
        assert await PD._list_paddle("/x") == []


class TestBuildPaddleDigest:
    @pytest.mark.asyncio
    async def test_full(self, monkeypatch):
        from datetime import UTC, datetime
        today = datetime.now(UTC).date().isoformat()
        _set(monkeypatch,
             paddle_price_tiers={"pri_1": "pro", "pri_2": "pro"})

        async def fake_list(path, params=None, per_page=100):
            return {
                "/customers": [{"id": "c1"}, {"id": "c2"}],
                "/subscriptions": [
                    {"status": "active",
                     "items": [
                         {"price": {"id": "pri_1",
                                    "unit_price": {"amount": 500}},
                          "quantity": 2},
                         {"price": {"id": "pri_2",
                                    "unit_price": {"amount": 300}}},
                         {"price": {}},
                     ]},
                    {"status": "trialing"},
                    {"status": "past_due"},
                    {"status": "canceled"},
                ],
                "/transactions": [
                    {"status": "paid",
                     "details": {"totals": {"total": 1000}},
                     "billed_at": today + "T10:00:00Z"},
                    {"status": "paid",
                     "details": {"totals": {"total": 2000}},
                     "created_at": "2020-01-01T00:00:00Z"},
                    {"status": "billed"},
                    {"status": "paid", "billed_at": ""},
                ],
                "/payouts": [
                    {"status": "paid",
                     "amounts": [{"amount": 5000,
                                  "currency_code": "USD"}]},
                    {"status": "scheduled",
                     "amounts": [{"amount": 800,
                                  "currency_code": "EUR"}]},
                    {"status": "failed"},
                ],
            }[path]

        monkeypatch.setattr(PD, "_list_paddle", fake_list)
        out = await PD.build_paddle_digest()
        assert "PRODUCTION" in out
        assert "Customers: *2*" in out
        assert "Active subscriptions: *1*" in out
        assert "Trialing: *1*" in out and "Past due: *1*" in out
        assert "pro: *2*" in out  # two items with known price ids
        assert "USD 30.00" in out  # 1000+2000 all-time
        assert "USD 10.00" in out  # today's revenue
        assert "USD 13.00" in out  # MRR 500*2 + 300*1
        assert "USD 50.00" in out  # paid payout
        assert "USD 8.00" in out   # scheduled payout
        assert "EUR, USD" in out

    @pytest.mark.asyncio
    async def test_empty(self, monkeypatch):
        _set(monkeypatch)
        monkeypatch.setattr(PD, "_list_paddle",
                            AsyncMock(return_value=[]))
        out = await PD.build_paddle_digest()
        assert "Customers: *0*" in out
        assert "USD 0.00" in out
        assert "Currencies" not in out


class TestBillingDispatch:
    @pytest.mark.asyncio
    async def test_paddle(self, monkeypatch):
        _set(monkeypatch)
        monkeypatch.setattr(PD, "build_paddle_digest",
                            AsyncMock(return_value="PADDLE"))
        assert await PD.build_billing_digest() == "PADDLE"

    @pytest.mark.asyncio
    async def test_polar(self, monkeypatch):
        _set(monkeypatch, billing_provider="polar")
        import app.services.polar_digest as polar
        monkeypatch.setattr(polar, "build_polar_digest",
                            AsyncMock(return_value="POLAR"))
        assert await PD.build_billing_digest() == "POLAR"

    @pytest.mark.asyncio
    async def test_dodo(self, monkeypatch):
        _set(monkeypatch, billing_provider="dodo")
        import app.services.dodo_digest as dodo
        monkeypatch.setattr(dodo, "build_dodo_digest",
                            AsyncMock(return_value="DODO"))
        assert await PD.build_billing_digest() == "DODO"


class TestSendToSlack:
    @pytest.mark.asyncio
    async def test_no_post(self, monkeypatch):
        monkeypatch.setattr(PD, "build_billing_digest",
                            AsyncMock(return_value="txt"))
        out = await PD.send_paddle_digest_to_slack(post_to_slack=False)
        assert out == {"text": "txt", "posted": False, "error": None}

    @pytest.mark.asyncio
    async def test_post_ok(self, monkeypatch):
        monkeypatch.setattr(PD, "build_billing_digest",
                            AsyncMock(return_value="txt"))
        import app.services.slack_notifications as sn
        monkeypatch.setattr(sn, "post_billing_digest_to_slack",
                            AsyncMock(return_value=(True, None)))
        out = await PD.send_paddle_digest_to_slack()
        assert out["posted"] is True

    @pytest.mark.asyncio
    async def test_post_error(self, monkeypatch):
        monkeypatch.setattr(PD, "build_billing_digest",
                            AsyncMock(return_value="txt"))
        import app.services.slack_notifications as sn
        monkeypatch.setattr(sn, "post_billing_digest_to_slack",
                            AsyncMock(return_value=(False, "webhook dead")))
        out = await PD.send_paddle_digest_to_slack()
        assert out["posted"] is False
        assert out["error"] == "webhook dead"
