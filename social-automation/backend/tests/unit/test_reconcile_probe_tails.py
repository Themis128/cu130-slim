"""Coverage tails: account_status_reconcile._probe platform branches."""

from unittest.mock import AsyncMock, MagicMock

import pytest


def _client(status=200, json_data=None):
    c = MagicMock()
    resp = MagicMock(status_code=status)
    resp.json = MagicMock(return_value=json_data or {})
    c.get = AsyncMock(return_value=resp)
    c.post = AsyncMock(return_value=resp)
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    return c


def _patch(monkeypatch, client):
    from app.worker.tasks import account_status_reconcile as M

    monkeypatch.setattr(M.httpx, "AsyncClient", lambda *a, **k: client)


@pytest.mark.asyncio
async def test_probe_instagram_business_login(monkeypatch):
    from app.worker.tasks import account_status_reconcile as M

    _patch(monkeypatch, _client(200))
    assert await M._probe("instagram", "1", "t", {"login_type": "business_login"}) == "active"


@pytest.mark.asyncio
async def test_probe_instagram_page_login(monkeypatch):
    from app.worker.tasks import account_status_reconcile as M

    _patch(monkeypatch, _client(403))
    assert await M._probe("instagram", "1", "t", {}) == "revoked"


@pytest.mark.asyncio
async def test_probe_whatsapp_verified(monkeypatch):
    from app.worker.tasks import account_status_reconcile as M

    _patch(monkeypatch, _client(200, {"code_verification_status": "VERIFIED"}))
    assert await M._probe("whatsapp", "1", "t", {"phone_number_id": "p"}) == "active"


@pytest.mark.asyncio
async def test_probe_whatsapp_not_verified(monkeypatch):
    from app.worker.tasks import account_status_reconcile as M

    _patch(monkeypatch, _client(200, {"code_verification_status": "NOT_VERIFIED"}))
    assert await M._probe("whatsapp", "1", "t", {"phone_number_id": "p"}) == "revoked"


@pytest.mark.asyncio
async def test_probe_whatsapp_other_status_inconclusive(monkeypatch):
    from app.worker.tasks import account_status_reconcile as M

    _patch(monkeypatch, _client(200, {"code_verification_status": "PENDING"}))
    assert await M._probe("whatsapp", "1", "t", {"phone_number_id": "p"}) == "inconclusive"


@pytest.mark.asyncio
async def test_probe_whatsapp_no_phone_id(monkeypatch):
    from app.worker.tasks import account_status_reconcile as M

    _patch(monkeypatch, _client(200))
    assert await M._probe("whatsapp", "1", "t", {}) == "inconclusive"


@pytest.mark.asyncio
async def test_probe_whatsapp_http_error_fallthrough(monkeypatch):
    from app.worker.tasks import account_status_reconcile as M

    _patch(monkeypatch, _client(401))
    assert await M._probe("whatsapp", "1", "t", {"phone_number_id": "p"}) == "revoked"


@pytest.mark.asyncio
async def test_probe_linkedin_active(monkeypatch):
    from app.worker.tasks import account_status_reconcile as M

    _patch(monkeypatch, _client(200))
    assert await M._probe("linkedin", "1", "t", {}) == "active"


@pytest.mark.asyncio
async def test_probe_twitter_401_inconclusive(monkeypatch):
    from app.worker.tasks import account_status_reconcile as M

    _patch(monkeypatch, _client(401))
    assert await M._probe("twitter", "1", "t", {}) == "inconclusive"


@pytest.mark.asyncio
async def test_probe_twitter_active(monkeypatch):
    from app.worker.tasks import account_status_reconcile as M

    _patch(monkeypatch, _client(200))
    assert await M._probe("twitter", "1", "t", {}) == "active"


@pytest.mark.asyncio
async def test_probe_telegram_active(monkeypatch):
    from app.worker.tasks import account_status_reconcile as M

    _patch(monkeypatch, _client(200))
    assert await M._probe("telegram", "1", "t", {}) == "active"


@pytest.mark.asyncio
async def test_probe_500_inconclusive(monkeypatch):
    from app.worker.tasks import account_status_reconcile as M

    _patch(monkeypatch, _client(500))
    assert await M._probe("facebook", "1", "t", {}) == "inconclusive"


@pytest.mark.asyncio
async def test_probe_exception_inconclusive(monkeypatch):
    from app.worker.tasks import account_status_reconcile as M

    c = _client()
    c.get = AsyncMock(side_effect=RuntimeError("net down"))
    _patch(monkeypatch, c)
    assert await M._probe("facebook", "1", "t", {}) == "inconclusive"
