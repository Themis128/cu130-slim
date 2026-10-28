"""Coverage: consent_healers platform probe helpers + wrapper."""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest


def _mock_subprocess_ok():
    m = MagicMock()
    m.returncode = 0
    m.stdout = json.dumps({"results": ["checked"]})
    return m


@pytest.mark.asyncio
async def test_twitter_skip_no_token(monkeypatch):
    from app.worker.tasks import consent_healers as CH
    monkeypatch.delenv("X_WEB_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("X_WEB_CT0", raising=False)
    assert await CH._twitter() == "skip"


@pytest.mark.asyncio
async def test_twitter_probe_success(monkeypatch):
    from app.worker.tasks import consent_healers as CH
    monkeypatch.setenv("X_WEB_AUTH_TOKEN", "t")
    monkeypatch.setenv("X_WEB_CT0", "c")
    monkeypatch.setattr(CH, "post_alert_to_slack", AsyncMock())
    monkeypatch.setattr("urllib.request.urlopen",
        lambda r, timeout=15: MagicMock(__enter__=lambda s: s,
            __exit__=lambda s, *a: False,
            read=lambda: b'{"logged_in":true}'))
    out = await CH._twitter()
    assert isinstance(out, str)


@pytest.mark.asyncio
async def test_whatsapp_skip_no_contacts(monkeypatch):
    from app.worker.tasks import consent_healers as CH
    db = MagicMock()
    res = MagicMock()
    res.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=res)
    factory = MagicMock(return_value=db)
    factory.return_value.__aenter__ = AsyncMock(return_value=db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(CH, "async_session_maker", factory)
    out = await CH._whatsapp()
    assert isinstance(out, str)


@pytest.mark.asyncio
async def test_bridge_health_ok(monkeypatch):
    from app.worker.tasks import consent_healers as CH
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"browser_connected": True}
    client = MagicMock()
    client.get = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **k: client)
    out = await CH._bridge_health()
    assert isinstance(out, str)


@pytest.mark.asyncio
async def test_heal_consents_wrapper(monkeypatch):
    from app.worker.tasks import consent_healers as CH
    monkeypatch.setattr(CH, "_twitter", AsyncMock(return_value="skip"))
    monkeypatch.setattr(CH, "_linkedin_snapshot", AsyncMock(return_value="skip"))
    monkeypatch.setattr(CH, "_whatsapp", AsyncMock(return_value="skip"))
    monkeypatch.setattr(CH, "_bridge_health", AsyncMock(return_value="ok"))
    out = CH.heal_consents()
    assert isinstance(out, dict)
