"""Coverage for consent_healers._linkedin_token_health dead-pair detector."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.worker.tasks.consent_healers as CH

pytestmark = pytest.mark.asyncio


class _AsyncCM:
    def __init__(self, v):
        self._v = v

    async def __aenter__(self):
        return self._v

    async def __aexit__(self, *a):
        return False


def _res(first=None):
    return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: first))


class _Resp:
    def __init__(self, status):
        self.status_code = status


class _Client:
    def __init__(self, status):
        self._status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None):
        return _Resp(self._status)


def _db_with(acc=None, acc2=None):
    return SimpleNamespace(
        execute=AsyncMock(return_value=_res(acc)),
        get=AsyncMock(return_value=acc2 or acc),
        commit=AsyncMock(),
    )


def _acc():
    return SimpleNamespace(id="a1", team_id="t1", access_token_enc=b"x", refresh_token_enc=b"r")


def _patch_common(monkeypatch, acc=_acc()):
    import httpx

    monkeypatch.setattr(CH, "task_session", lambda: _AsyncCM(_db_with(acc)))
    monkeypatch.setattr(CH, "decrypt_token", lambda b: "tok")
    return httpx


async def test_health_skip_no_account(monkeypatch):
    _patch_common(monkeypatch, acc=None)
    assert await CH._linkedin_token_health() == "skip"


async def test_health_ok_200(monkeypatch):
    httpx = _patch_common(monkeypatch)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: _Client(200))
    assert await CH._linkedin_token_health() == "ok"


async def test_health_probe_other(monkeypatch):
    httpx = _patch_common(monkeypatch)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: _Client(500))
    assert await CH._linkedin_token_health() == "probe-500"


async def test_health_refreshed(monkeypatch):
    acc = _acc()
    acc2 = SimpleNamespace(id="a1", access_token_enc=None, refresh_token_enc=None)
    import httpx

    monkeypatch.setattr(CH, "task_session", lambda: _AsyncCM(_db_with(acc, acc2)))
    monkeypatch.setattr(CH, "decrypt_token", lambda b: "tok")
    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: _Client(401))
    import app.api.auth as auth
    monkeypatch.setattr(auth, "linkedin_client", SimpleNamespace(
        refresh_token=AsyncMock(return_value={"access_token": "new", "refresh_token": "nr"})))
    import app.core.security as sec
    monkeypatch.setattr(sec, "encrypt_token", lambda t: b"enc")
    assert await CH._linkedin_token_health() == "refreshed"
    assert acc2.access_token_enc == b"enc" and acc2.refresh_token_enc == b"enc"


async def test_health_dead_pair(monkeypatch):
    acc = _acc()
    import httpx

    monkeypatch.setattr(CH, "task_session", lambda: _AsyncCM(_db_with(acc)))
    monkeypatch.setattr(CH, "decrypt_token", lambda b: "tok")
    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: _Client(401))
    import app.api.auth as auth
    monkeypatch.setattr(auth, "linkedin_client", SimpleNamespace(
        refresh_token=AsyncMock(side_effect=Exception("invalid_grant")),
        get_authorization_url=AsyncMock(return_value="https://li/auth"),
    ))
    monkeypatch.setattr(auth, "_linkedin_scopes", lambda: "s")
    import app.core.config as cfg
    monkeypatch.setattr(cfg, "get_settings", lambda: SimpleNamespace(LINKEDIN_REDIRECT_URI="https://cb"))
    import app.core.security as sec
    monkeypatch.setattr(sec, "sign_oauth_state", lambda d: "st")
    monkeypatch.setattr(CH, "post_alert_to_slack", AsyncMock())
    import app.services.slack_notifications as SN
    monkeypatch.setattr(SN, "send_telegram_alert", AsyncMock())
    out = await CH._linkedin_token_health()
    assert out == "dead_pair" and CH.post_alert_to_slack.called
