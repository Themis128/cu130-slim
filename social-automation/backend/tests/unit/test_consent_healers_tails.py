"""Coverage tails: consent_healers — linkedin snapshot, whatsapp, bridge, dmr."""
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


def _client(status=200, json_data=None):
    c = MagicMock()
    resp = MagicMock(status_code=status)
    resp.json = MagicMock(return_value=json_data or {})
    c.get = AsyncMock(return_value=resp)
    c.post = AsyncMock(return_value=resp)
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    return c


# ---- _linkedin_snapshot ----

@pytest.mark.asyncio
async def test_li_no_grant(monkeypatch):
    from app.worker.tasks import consent_healers as M
    acc = MagicMock(access_token_enc=b"e")
    monkeypatch.setattr(M, "task_session", lambda: _db([acc]))
    monkeypatch.setattr(M, "decrypt_token", lambda b: "t")
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda *a, **k: _client(403))
    assert (await M._linkedin_snapshot()).startswith("no-grant")


@pytest.mark.asyncio
async def test_li_no_emails(monkeypatch):
    from app.worker.tasks import consent_healers as M
    acc = MagicMock(access_token_enc=b"e")
    monkeypatch.setattr(M, "task_session", lambda: _db([acc]))
    monkeypatch.setattr(M, "decrypt_token", lambda b: "t")
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda *a, **k: _client(200, {"elements": []}))
    assert await M._linkedin_snapshot() == "granted-no-emails"


@pytest.mark.asyncio
async def test_li_attaches(monkeypatch):
    from app.worker.tasks import consent_healers as M
    acc = MagicMock(access_token_enc=b"e")
    monkeypatch.setattr(M, "task_session", lambda: _db([acc]))
    monkeypatch.setattr(M, "decrypt_token", lambda b: "t")
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda *a, **k: _client(
        200, {"elements": [{"emailAddress": "a@b.co"}, {"emailAddress": "x@linkedin.com"}]}))
    run = MagicMock()
    monkeypatch.setattr("subprocess.run", run)
    posted = []
    monkeypatch.setattr(M, "post_alert_to_slack",
                        AsyncMock(side_effect=lambda m: posted.append(m)))
    out = await M._linkedin_snapshot()
    assert out == "attached 1" and posted and run.called


@pytest.mark.asyncio
async def test_li_decrypt_fail(monkeypatch):
    from app.worker.tasks import consent_healers as M
    acc = MagicMock(access_token_enc=b"e")
    monkeypatch.setattr(M, "task_session", lambda: _db([acc]))
    monkeypatch.setattr(M, "decrypt_token", MagicMock(side_effect=ValueError("bad")))
    assert await M._linkedin_snapshot() == "skip"


# ---- _whatsapp ----

@pytest.mark.asyncio
async def test_wa_env_skip(monkeypatch):
    from app.worker.tasks import consent_healers as M
    monkeypatch.delenv("WHATSAPP_PHONE_NUMBER_ID", raising=False)
    monkeypatch.delenv("WHATSAPP_ACCESS_TOKEN", raising=False)
    assert await M._whatsapp() == "skip"


@pytest.mark.asyncio
async def test_wa_probe_fail(monkeypatch):
    from app.worker.tasks import consent_healers as M
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "p")
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "t")
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda *a, **k: _client(500))
    assert await M._whatsapp() == "probe-fail"


@pytest.mark.asyncio
async def test_wa_verified_alerts(monkeypatch):
    from app.worker.tasks import consent_healers as M
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "p")
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "t")
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda *a, **k: _client(200, {"code_verification_status": "VERIFIED"}))
    posted = []
    monkeypatch.setattr(M, "post_alert_to_slack",
                        AsyncMock(side_effect=lambda m: posted.append(m)))
    assert await M._whatsapp() == "verified" and posted


@pytest.mark.asyncio
async def test_wa_still_pending(monkeypatch):
    from app.worker.tasks import consent_healers as M
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "p")
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "t")
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda *a, **k: _client(200, {"code_verification_status": "NOT_VERIFIED"}))
    assert (await M._whatsapp()) == "still-NOT_VERIFIED"


# ---- _bridge_health ----

@pytest.mark.asyncio
async def test_bridge_skip_no_env(monkeypatch):
    from app.worker.tasks import consent_healers as M
    monkeypatch.delenv("X_WEB_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("X_WEB_CT0", raising=False)
    assert await M._bridge_health() == "skip"


@pytest.mark.asyncio
async def test_bridge_stale_alerts(monkeypatch):
    from app.worker.tasks import consent_healers as M
    monkeypatch.setenv("X_WEB_AUTH_TOKEN", "t")
    monkeypatch.setenv("X_WEB_CT0", "c")
    calls = {"n": 0}

    def _urlopen(req, timeout=None):
        calls["n"] += 1
        r = MagicMock()
        r.read.return_value = b'{"result": "https://x.com/login"}' if calls["n"] > 2 else b"{}"
        r.__enter__ = lambda s: s
        r.__exit__ = lambda *a: False
        return r

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    import time as _t
    monkeypatch.setattr(_t, "sleep", lambda s: None)
    posted = []
    monkeypatch.setattr(M, "post_alert_to_slack",
                        AsyncMock(side_effect=lambda m: posted.append(m)))
    out = await M._bridge_health()
    assert out in ("stale", "live", "rotated (https://x.com/login") or "busy" in out


@pytest.mark.asyncio
async def test_bridge_busy(monkeypatch):
    from app.worker.tasks import consent_healers as M
    monkeypatch.setenv("X_WEB_AUTH_TOKEN", "t")
    monkeypatch.setenv("X_WEB_CT0", "c")
    monkeypatch.setattr("urllib.request.urlopen",
                        MagicMock(side_effect=RuntimeError("conflict")))
    assert (await M._bridge_health()).startswith("busy")


# ---- _dmr_health ----

@pytest.mark.asyncio
async def test_dmr_warm(monkeypatch):
    from app.worker.tasks import consent_healers as M
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda *a, **k: _client(200))
    assert await M._dmr_health() == "warm"


@pytest.mark.asyncio
async def test_dmr_http_error(monkeypatch):
    from app.worker.tasks import consent_healers as M
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda *a, **k: _client(503))
    assert (await M._dmr_health()) == "http 503"


@pytest.mark.asyncio
async def test_dmr_exception_alerts(monkeypatch):
    from app.worker.tasks import consent_healers as M
    c = _client()
    c.post = AsyncMock(side_effect=RuntimeError("hang"))
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda *a, **k: c)
    posted = []
    monkeypatch.setattr(M, "post_alert_to_slack",
                        AsyncMock(side_effect=lambda m: posted.append(m)))
    out = await M._dmr_health()
    assert posted and out.startswith("down (")


# ---- wrapper ----

def test_heal_consents_wrapper(monkeypatch):
    from app.worker.tasks import consent_healers as M
    monkeypatch.setattr(M, "run_async", lambda c: (c.close(), {"ok": 1})[1])
    assert M.heal_consents() == {"ok": 1}
