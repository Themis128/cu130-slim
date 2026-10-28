"""Coverage: consent_healers platform probes + wrapper."""

from unittest.mock import AsyncMock, MagicMock

import pytest


def _db(accs):
    db = MagicMock()
    res = MagicMock()
    res.scalars.return_value.first.return_value = accs[0] if accs else None
    db.execute = AsyncMock(return_value=res)
    db.commit = AsyncMock()
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=False)
    return db


@pytest.mark.asyncio
async def test_twitter_skip_no_account(monkeypatch):
    from app.worker.tasks import consent_healers as CH

    monkeypatch.setattr(CH, "task_session", lambda: _db([]))
    assert await CH._twitter() == "skip"


@pytest.mark.asyncio
async def test_twitter_healed(monkeypatch):
    from app.worker.tasks import consent_healers as CH

    acc = MagicMock(status="expired", access_token_enc=b"enc")
    monkeypatch.setattr(CH, "task_session", lambda: _db([acc]))
    monkeypatch.setattr(CH, "decrypt_token", lambda b: "tok")
    monkeypatch.setattr(CH, "post_alert_to_slack", AsyncMock())
    resp = MagicMock(status_code=200)
    client = MagicMock()
    client.get = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(CH.httpx, "AsyncClient", lambda *a, **k: client)
    assert await CH._twitter() == "healed" and acc.status == "active"


@pytest.mark.asyncio
async def test_whatsapp_skip_no_account(monkeypatch):
    from app.worker.tasks import consent_healers as CH

    monkeypatch.setattr(CH, "task_session", lambda: _db([]))
    out = await CH._whatsapp()
    assert isinstance(out, str)


@pytest.mark.asyncio
async def test_heal_consents_wrapper(monkeypatch):
    from app.worker.tasks import consent_healers as CH

    monkeypatch.setattr(CH, "run_async", lambda c: {"twitter": "skip"})
    out = CH.heal_consents()
    assert isinstance(out, dict)
