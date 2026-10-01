"""Unit tests for the Polar + Dodo Slack usage digests. No network."""
from datetime import UTC, datetime

import pytest

from app.core.config import Settings
from app.services import dodo_api, dodo_digest, polar_api, polar_digest


def _polar_settings(**over):
    kw = {"POLAR_ACCESS_TOKEN": "tok", "POLAR_ENVIRONMENT": "sandbox",
          "POLAR_PRODUCT_PRO": "pp", "POLAR_PRODUCT_BUSINESS": "pb",
          "POLAR_PRODUCT_ENTERPRISE": "pe"}
    kw.update(over)
    return Settings(**kw)


def _dodo_settings(**over):
    kw = {"DODO_PAYMENTS_API_KEY": "key", "DODO_ENVIRONMENT": "test_mode",
          "DODO_PRODUCT_PRO": "dp", "DODO_PRODUCT_BUSINESS": "db",
          "DODO_PRODUCT_ENTERPRISE": "de"}
    kw.update(over)
    return Settings(**kw)


class TestMoney:
    def test_none_and_value(self):
        assert polar_digest._money(None) == "N/A"
        assert polar_digest._money(123456) == "USD 1,234.56"
        assert dodo_digest._money(None) == "N/A"
        assert dodo_digest._money(500) == "USD 5.00"


class TestListHelpers:
    @pytest.mark.asyncio
    async def test_polar_no_token_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            polar_digest, "get_settings",
            lambda: _polar_settings(POLAR_ACCESS_TOKEN=""),
        )
        assert await polar_digest._list_polar("/customers/") == []

    @pytest.mark.asyncio
    async def test_polar_error_returns_empty(self, monkeypatch):
        monkeypatch.setattr(polar_digest, "get_settings", lambda: _polar_settings())

        async def boom(*a, **kw):
            raise polar_api.PolarError("down")

        monkeypatch.setattr(polar_digest, "_request", boom)
        assert await polar_digest._list_polar("/orders/") == []

    @pytest.mark.asyncio
    async def test_polar_items_and_data_fallback(self, monkeypatch):
        monkeypatch.setattr(polar_digest, "get_settings", lambda: _polar_settings())

        async def items_req(*a, **kw):
            return {"items": [{"id": 1}]}

        monkeypatch.setattr(polar_digest, "_request", items_req)
        assert await polar_digest._list_polar("/x") == [{"id": 1}]

        async def data_req(*a, **kw):
            return {"data": [{"id": 2}]}

        monkeypatch.setattr(polar_digest, "_request", data_req)
        assert await polar_digest._list_polar("/x") == [{"id": 2}]

        async def empty_req(*a, **kw):
            return {}

        monkeypatch.setattr(polar_digest, "_request", empty_req)
        assert await polar_digest._list_polar("/x") == []

    @pytest.mark.asyncio
    async def test_dodo_no_token_and_error(self, monkeypatch):
        monkeypatch.setattr(
            dodo_digest, "get_settings",
            lambda: _dodo_settings(DODO_PAYMENTS_API_KEY=""),
        )
        assert await dodo_digest._list_dodo("/customers") == []

        monkeypatch.setattr(dodo_digest, "get_settings", lambda: _dodo_settings())

        async def boom(*a, **kw):
            raise dodo_api.DodoError("down")

        monkeypatch.setattr(dodo_digest, "_request", boom)
        assert await dodo_digest._list_dodo("/payments") == []

    @pytest.mark.asyncio
    async def test_dodo_items_and_data(self, monkeypatch):
        monkeypatch.setattr(dodo_digest, "get_settings", lambda: _dodo_settings())

        async def items_req(*a, **kw):
            return {"items": [{"id": 1}]}

        monkeypatch.setattr(dodo_digest, "_request", items_req)
        assert await dodo_digest._list_dodo("/x") == [{"id": 1}]

        async def data_req(*a, **kw):
            return {"data": [{"id": 2}]}

        monkeypatch.setattr(dodo_digest, "_request", data_req)
        assert await dodo_digest._list_dodo("/x") == [{"id": 2}]


def _patch_lists(monkeypatch, module, name, data):
    """Return canned lists keyed by path for _list_polar / _list_dodo."""
    async def fake(path, params=None, per_page=100):
        return data.get(path, [])

    monkeypatch.setattr(module, name, fake)


class TestPolarDigest:
    @pytest.mark.asyncio
    async def test_full_digest(self, monkeypatch):
        today = datetime.now(UTC).date().isoformat()
        monkeypatch.setattr(polar_digest, "get_settings", lambda: _polar_settings())
        _patch_lists(monkeypatch, polar_digest, "_list_polar", {
            "/customers/": [{"id": "c1"}, {"id": "c2"}],
            "/subscriptions/": [
                {"status": "active", "amount": 12000, "product_id": "pp"},
                {"status": "active", "amount": 120000,
                 "recurring_interval": "year", "recurring_interval_count": 1,
                 "product": {"id": "pb"}},
                {"status": "active", "amount": 1000,
                 "recurring_interval": "week", "recurring_interval_count": 2,
                 "product_id": "px_unmapped"},
                {"status": "active", "amount": 0},  # no product id → skipped
                {"status": "trialing"},
                {"status": "past_due"},
                {"status": "canceled"},
                {"status": "unpaid"},
            ],
            "/orders/": [
                {"paid": True, "total_amount": 5000, "created_at": today + "T01:00:00Z"},
                {"status": "paid", "total_amount": 3000, "created_at": "2020-01-01"},
                {"status": "pending", "total_amount": 9999},
            ],
        })
        text = await polar_digest.build_polar_digest()
        assert "*Polar usage report* · SANDBOX" in text
        assert "Customers: *2*" in text
        assert "Active subscriptions: *4*" in text
        assert "Trialing: *1* · Past due: *1* · Canceled: *2*" in text
        assert "- pro: *1*" in text
        assert "- business: *1*" in text
        assert "- unknown: *1*" in text
        assert "All-time paid: *2* orders · *USD 80.00*" in text
        assert "Today: *1* orders · *USD 50.00*" in text
        # MRR: 12000 + 120000/12 + int(1000*4.33/2) + 0 = 12000+10000+2165 = 24165
        assert "*USD 241.65*" in text
        assert "Stripe Connect" in text

    @pytest.mark.asyncio
    async def test_empty_digest_no_tier_section(self, monkeypatch):
        monkeypatch.setattr(polar_digest, "get_settings", lambda: _polar_settings())
        _patch_lists(monkeypatch, polar_digest, "_list_polar", {})
        text = await polar_digest.build_polar_digest()
        assert "*Active subs by tier*" not in text
        assert "Customers: *0*" in text
        assert "USD 0.00" in text

    @pytest.mark.asyncio
    async def test_no_token_empty_sections(self, monkeypatch):
        monkeypatch.setattr(
            polar_digest, "get_settings",
            lambda: _polar_settings(POLAR_ACCESS_TOKEN=""),
        )
        text = await polar_digest.build_polar_digest()
        assert "Customers: *0*" in text


class TestDodoDigest:
    @pytest.mark.asyncio
    async def test_full_digest(self, monkeypatch):
        today = datetime.now(UTC).date().isoformat()
        monkeypatch.setattr(dodo_digest, "get_settings", lambda: _dodo_settings())
        _patch_lists(monkeypatch, dodo_digest, "_list_dodo", {
            "/customers": [{"id": "c1"}],
            "/subscriptions": [
                {"status": "active", "recurring_pre_tax_amount": 6000,
                 "product_id": "dp"},
                {"status": "active", "recurring_pre_tax_amount": 60000,
                 "payment_frequency_interval": "Year",
                 "payment_frequency_count": 1, "product_id": "db"},
                {"status": "active", "recurring_pre_tax_amount": 500,
                 "payment_frequency_interval": "Week",
                 "payment_frequency_count": 1},
                {"status": "on_hold"},
                {"status": "paused"},
                {"status": "cancelled"},
                {"status": "expired"},
            ],
            "/payments": [
                {"status": "succeeded", "total_amount": 7000,
                 "created_at": today + "T02:00:00Z"},
                {"status": "succeeded", "total_amount": 1000,
                 "created_at": "2020-05-05"},
                {"status": "failed", "total_amount": 9999},
            ],
        })
        text = await dodo_digest.build_dodo_digest()
        assert "*Dodo usage report* · TEST MODE" in text
        assert "Customers: *1*" in text
        assert "Active subscriptions: *3*" in text
        assert "On hold: *1* · Paused: *1* · Cancelled: *2*" in text
        assert "- pro: *1*" in text
        assert "- business: *1*" in text
        assert "All-time: *2* payments · *USD 80.00*" in text
        assert "Today: *1* payments · *USD 70.00*" in text
        # MRR: 6000 + 60000/12 + int(500*4.33) = 6000+5000+2165 = 13165
        assert "*USD 131.65*" in text
        assert "settles directly to your bank" in text

    @pytest.mark.asyncio
    async def test_empty_digest(self, monkeypatch):
        monkeypatch.setattr(dodo_digest, "get_settings", lambda: _dodo_settings())
        _patch_lists(monkeypatch, dodo_digest, "_list_dodo", {})
        text = await dodo_digest.build_dodo_digest()
        assert "*Active subs by tier*" not in text
        assert "USD 0.00" in text
