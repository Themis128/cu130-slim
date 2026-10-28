"""Unit tests for app/services/dodo_digest.py."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.dodo_digest as DD
from app.services.dodo_api import DodoError


def _settings(**over):
    base = dict(DODO_PAYMENTS_API_KEY="k", DODO_ENVIRONMENT="live_mode",
                dodo_product_tiers={"p1": "pro", "p2": "starter"})
    base.update(over)
    return SimpleNamespace(**base)


def test_money():
    assert DD._money(None) == "N/A"
    assert DD._money(123456) == "USD 1,234.56"
    assert DD._money(500, "EUR") == "EUR 5.00"


@pytest.mark.asyncio
async def test_list_dodo_no_key(monkeypatch):
    monkeypatch.setattr(DD, "get_settings",
                        lambda: _settings(DODO_PAYMENTS_API_KEY=""))
    assert await DD._list_dodo("/customers") == []


@pytest.mark.asyncio
async def test_list_dodo_error_and_shapes(monkeypatch):
    monkeypatch.setattr(DD, "get_settings", lambda: _settings())

    req = AsyncMock(side_effect=DodoError("down"))
    monkeypatch.setattr(DD, "_request", req)
    assert await DD._list_dodo("/customers") == []

    req = AsyncMock(return_value={"items": [{"a": 1}]})
    monkeypatch.setattr(DD, "_request", req)
    assert await DD._list_dodo("/customers") == [{"a": 1}]
    assert req.await_args.args[0] == "GET"
    assert req.await_args.kwargs["params"]["page_size"] == "100"

    req = AsyncMock(return_value={"data": [{"b": 2}]})
    monkeypatch.setattr(DD, "_request", req)
    assert await DD._list_dodo("/x") == [{"b": 2}]

    req = AsyncMock(return_value={})
    monkeypatch.setattr(DD, "_request", req)
    assert await DD._list_dodo("/x") == []


@pytest.mark.asyncio
async def test_build_digest(monkeypatch):
    monkeypatch.setattr(DD, "get_settings", lambda: _settings())
    today = datetime.now(UTC).date().isoformat()
    subs = [
        {"status": "active", "product_id": "p1",
         "recurring_pre_tax_amount": 12000,
         "payment_frequency_interval": "Month",
         "payment_frequency_count": 1},
        {"status": "active", "product_id": "p2",
         "recurring_pre_tax_amount": 120000,
         "payment_frequency_interval": "Year",
         "payment_frequency_count": 1},
        {"status": "active", "product_id": None,
         "recurring_pre_tax_amount": 433,
         "payment_frequency_interval": "Week",
         "payment_frequency_count": 1},
        {"status": "on_hold"}, {"status": "paused"},
        {"status": "cancelled"}, {"status": "expired"},
    ]
    payments = [
        {"status": "succeeded", "total_amount": 10000,
         "created_at": f"{today}T10:00:00Z"},
        {"status": "succeeded", "total_amount": 5000,
         "created_at": "2020-01-01T00:00:00Z"},
        {"status": "failed", "total_amount": 9999},
        {"status": "succeeded"},
    ]
    calls = []
    async def _list(path, params=None):
        calls.append(path)
        return {"/customers": [{"c": 1}],
                "/subscriptions": subs,
                "/payments": payments}[path]
    monkeypatch.setattr(DD, "_list_dodo", _list)
    out = await DD.build_dodo_digest()
    assert "LIVE MODE" in out
    assert "Customers: *1*" in out
    assert "Active subscriptions: *3*" in out
    assert "On hold: *1*" in out and "Cancelled: *2*" in out
    assert "pro: *1*" in out and "starter: *1*" in out
    assert "USD 150.00" in out          # all-time revenue
    assert "Today: *1* payments" in out
    assert calls == ["/customers", "/subscriptions", "/payments"]
