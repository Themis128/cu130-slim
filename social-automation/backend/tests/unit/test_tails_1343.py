"""Tail coverage for #1343 — the last branch arcs."""

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


def _res(first=None, all_=None):
    return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: first, all=lambda: all_ or []))


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._p = payload or {}

    def json(self):
        return self._p


class _Client:
    def __init__(self, resp=None):
        self._resp = resp or _Resp()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        return self._resp

    async def post(self, url, **kw):
        return self._resp


# ── linkedin_advanced.stream_conversion — no email/lead_id arcs ─────────


async def test_stream_conversion_minimal(monkeypatch):
    import httpx

    import app.services.linkedin_advanced as A

    monkeypatch.setattr(A, "_ctx", AsyncMock(return_value=("tok", "org")))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _Client(_Resp(201)))
    out = await A.stream_conversion(conversion_rule="audit_booked", happened_at_ms=1)
    assert out is not None


# ── linkedin_events.list_org_events — no-token skip ─────────────────────


async def test_list_org_events_skip(monkeypatch):
    import app.services.linkedin_events as E

    monkeypatch.setattr(E, "_org_token", AsyncMock(return_value=None))
    assert await E.list_org_events() == {"skip": "no-linkedin-org-account"}


# ── linkedin_lead_boost — no-org skip + empty winners ───────────────────


async def test_route_leads_no_org(monkeypatch):
    import app.worker.tasks.linkedin_lead_boost as LB

    monkeypatch.setattr(LB, "ENABLED", True)
    db = SimpleNamespace(execute=AsyncMock(return_value=_res(first=None)))
    monkeypatch.setattr(LB, "task_session", lambda: _AsyncCM(db))
    out = await LB._route_linkedin_leads()
    assert out["skip"] == "no-linkedin-org-account"


async def test_boost_empty_winners(monkeypatch):
    import app.worker.tasks.linkedin_lead_boost as LB

    monkeypatch.setattr(LB, "ENABLED", True)
    db = SimpleNamespace(execute=AsyncMock(return_value=_res(all_=[])), commit=AsyncMock())
    monkeypatch.setattr(LB, "task_session", lambda: _AsyncCM(db))
    assert await LB._boost_linkedin_winners() == {"boosted": 0}


# ── consent_healers._linkedin_token_health — remaining arms ─────────────


def _patch_health(monkeypatch, acc, refresh=None, rt_enc=b"r"):
    import httpx

    acc = acc or SimpleNamespace(id="a1", team_id="t1", access_token_enc=b"x", refresh_token_enc=rt_enc)
    db = SimpleNamespace(execute=AsyncMock(return_value=_res(first=acc)), get=AsyncMock(return_value=SimpleNamespace(id="a1")), commit=AsyncMock())
    monkeypatch.setattr(CH, "task_session", lambda: _AsyncCM(db))
    monkeypatch.setattr(CH, "decrypt_token", lambda b: "tok")
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _Client(_Resp(401)))
    import app.api.auth as auth

    monkeypatch.setattr(
        auth, "linkedin_client", SimpleNamespace(refresh_token=AsyncMock(return_value=refresh), get_authorization_url=AsyncMock(return_value="https://li/auth"))
    )
    monkeypatch.setattr(auth, "_linkedin_scopes", lambda: "s")
    import app.core.config as cfg

    monkeypatch.setattr(cfg, "get_settings", lambda: SimpleNamespace(LINKEDIN_REDIRECT_URI="cb"))
    import app.core.security as sec

    monkeypatch.setattr(sec, "sign_oauth_state", lambda d: "st")
    monkeypatch.setattr(sec, "encrypt_token", lambda t: b"enc")
    monkeypatch.setattr(CH, "post_alert_to_slack", AsyncMock())


async def test_health_dead_pair_no_rt(monkeypatch):
    _patch_health(monkeypatch, None, rt_enc=None)
    import app.services.slack_notifications as SN

    monkeypatch.setattr(SN, "send_telegram_alert", AsyncMock())
    assert await CH._linkedin_token_health() == "dead_pair"


async def test_health_refreshed_no_new_rt(monkeypatch):
    acc2 = SimpleNamespace(id="a1", access_token_enc=None, refresh_token_enc="keep")
    acc = SimpleNamespace(id="a1", team_id="t1", access_token_enc=b"x", refresh_token_enc=b"r")
    import httpx

    db = SimpleNamespace(execute=AsyncMock(return_value=_res(first=acc)), get=AsyncMock(return_value=acc2), commit=AsyncMock())
    monkeypatch.setattr(CH, "task_session", lambda: _AsyncCM(db))
    monkeypatch.setattr(CH, "decrypt_token", lambda b: "tok")
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _Client(_Resp(401)))
    import app.api.auth as auth

    monkeypatch.setattr(auth, "linkedin_client", SimpleNamespace(refresh_token=AsyncMock(return_value={"access_token": "new"})))
    import app.core.security as sec

    monkeypatch.setattr(sec, "encrypt_token", lambda t: b"enc")
    assert await CH._linkedin_token_health() == "refreshed"
    assert acc2.access_token_enc == b"enc" and acc2.refresh_token_enc == "keep"


async def test_health_telegram_alert_fails(monkeypatch):
    _patch_health(monkeypatch, None, refresh=None)
    import app.services.slack_notifications as SN

    monkeypatch.setattr(SN, "send_telegram_alert", AsyncMock(side_effect=Exception("tg down")))
    assert await CH._linkedin_token_health() == "dead_pair"


# ── consent_healers._x_credentials_login — empty-email continue arc ─────


async def test_x_login_empty_email(monkeypatch):
    monkeypatch.setenv("TWITTER_LOGIN_USERNAME", "u")
    monkeypatch.setenv("TWITTER_LOGIN_PASSWORD", "p")
    monkeypatch.delenv("TWITTER_LOGIN_EMAIL", raising=False)
    monkeypatch.delenv("TWITTER_TOTP_SECRET", raising=False)
    monkeypatch.setattr(CH.asyncio, "sleep", AsyncMock())
    c = SimpleNamespace(post=AsyncMock(return_value=_Resp()))
    await CH._x_credentials_login(c, "http://bridge")
    assert c.post.await_count >= 4
