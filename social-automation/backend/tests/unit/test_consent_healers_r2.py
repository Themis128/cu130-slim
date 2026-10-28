"""Coverage: consent_healers _linkedin_snapshot + _bridge_health bodies."""
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
async def test_linkedin_snapshot_skip_no_account(monkeypatch):
    from app.worker.tasks import consent_healers as CH
    monkeypatch.setattr(CH, "task_session", lambda: _db([]))
    out = await CH._linkedin_snapshot()
    assert out == "skip"


@pytest.mark.asyncio
async def test_linkedin_snapshot_granted(monkeypatch):
    from app.worker.tasks import consent_healers as CH
    acc = MagicMock(status="active", access_token_enc=b"enc", platform="linkedin")
    monkeypatch.setattr(CH, "task_session", lambda: _db([acc]))
    monkeypatch.setattr(CH, "decrypt_token", lambda b: "tok")
    monkeypatch.setattr(CH, "post_alert_to_slack", AsyncMock())
    monkeypatch.setattr("subprocess.run", lambda *a, **k: MagicMock(returncode=0))
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"elements": []}
    client = MagicMock()
    client.get = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(CH.httpx, "AsyncClient", lambda *a, **k: client)
    out = await CH._linkedin_snapshot()
    assert isinstance(out, str)


@pytest.mark.asyncio
async def test_bridge_health_skip_no_cookies(monkeypatch):
    from app.worker.tasks import consent_healers as CH
    monkeypatch.delenv("X_WEB_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("X_WEB_CT0", raising=False)
    assert await CH._bridge_health() == "skip"


@pytest.mark.asyncio
async def test_bridge_health_live(monkeypatch):
    from app.worker.tasks import consent_healers as CH
    monkeypatch.setenv("X_WEB_AUTH_TOKEN", "t")
    monkeypatch.setenv("X_WEB_CT0", "c")
    monkeypatch.setattr(CH, "post_alert_to_slack", AsyncMock())
    calls = []

    def fake_urlopen(req, timeout=15):
        calls.append(req.full_url)
        return MagicMock(__enter__=lambda s: s,
                         __exit__=lambda s, *a: False,
                         read=lambda: b'{"result":"https://x.com/home"}')
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    out = await CH._bridge_health()
    assert isinstance(out, str)
    assert any("/keepawake" in u for u in calls)


@pytest.mark.asyncio
async def test_bridge_health_stale(monkeypatch):
    from app.worker.tasks import consent_healers as CH
    monkeypatch.setenv("X_WEB_AUTH_TOKEN", "t")
    monkeypatch.setenv("X_WEB_CT0", "c")
    monkeypatch.setattr(CH, "post_alert_to_slack", AsyncMock())
    monkeypatch.setattr("urllib.request.urlopen",
        lambda req, timeout=15: MagicMock(
            __enter__=lambda s: s, __exit__=lambda s, *a: False,
            read=lambda: b'{"result":"https://x.com/i/flow/login"}'))
    out = await CH._bridge_health()
    assert out == "stale"
