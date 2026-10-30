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


@pytest.mark.asyncio
async def test_bridge_health_onboarding_url_is_stale(monkeypatch):
    """Regression: X login funnel /i/jf/onboarding/web?mode=login must read
    as 'stale' — flagged in Devin Review #1074."""
    import json

    from app.worker.tasks import consent_healers as CH

    monkeypatch.setenv("X_WEB_AUTH_TOKEN", "tok")
    monkeypatch.setenv("X_WEB_CT0", "ct0")
    monkeypatch.setattr(CH, "post_alert_to_slack", AsyncMock())

    calls = {"n": 0}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"{}"

    def fake_urlopen(req, timeout=15):
        calls["n"] += 1
        url = req.full_url if hasattr(req, "full_url") else req
        body = json.loads(req.data or b"{}")

        class R(_Resp):
            def read(self):
                if "/session/evaluate" in url and body.get("expression") == "location.href":
                    return json.dumps({"result": "https://x.com/i/jf/onboarding/web?mode=login"}).encode()
                return b"{}"

        return R()

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda s: None)

    out = await CH._bridge_health()
    assert out == "stale"
