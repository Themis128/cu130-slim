"""send_telegram_alert routing, celery task_failure notifier, OIDC SSO endpoints."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx
from fastapi import HTTPException

import app.services.slack_notifications as SN
import app.services.telegram_agent as TA
import app.worker._async as WA
from app.api import auth as AU
from app.worker import celery_app as CA

# ── send_telegram_alert ───────────────────────────────────────────────────


def _sess(acc):
    db = MagicMock()
    res = MagicMock()
    res.scalars.return_value.first.return_value = acc
    db.execute = AsyncMock(return_value=res)
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=False)
    return lambda: db


class _Client:
    sent: list = []

    def __init__(self, token):
        self.token = token

    async def send_message(self, chat, text):
        _Client.sent.append((chat, text))


@pytest.fixture
def tg(monkeypatch):
    _Client.sent = []
    monkeypatch.setattr(TA, "TelegramAPIClient", _Client)
    monkeypatch.setattr(TA, "_bot_token", lambda acc: acc.tok)
    return monkeypatch


@pytest.mark.asyncio
async def test_alert_critical_also_ops(tg):
    tg.setattr(WA, "task_session", _sess(SimpleNamespace(tok="t")))
    assert await SN.send_telegram_alert("critical", "down") is True
    assert _Client.sent == [(SN.TG_CHANNELS["critical"], "down"), (SN.TG_CHANNELS["ops"], "[P1] down")]


@pytest.mark.asyncio
async def test_alert_unknown_severity_falls_back_to_ops(tg):
    tg.setattr(WA, "task_session", _sess(SimpleNamespace(tok="t")))
    assert await SN.send_telegram_alert("weird", "hi") is True
    assert _Client.sent == [(SN.TG_CHANNELS["ops"], "hi")]


@pytest.mark.asyncio
@pytest.mark.parametrize("acc", [None, SimpleNamespace(tok="")])
async def test_alert_no_token(tg, acc):
    tg.setattr(WA, "task_session", _sess(acc))
    assert await SN.send_telegram_alert("intel", "i") is False


@pytest.mark.asyncio
async def test_alert_send_error(tg):
    def bad():
        raise RuntimeError("db")

    tg.setattr(WA, "task_session", bad)
    assert await SN.send_telegram_alert("ops", "e") is False


# ── celery task_failure notifier ──────────────────────────────────────────


def test_task_failure_runs_alert_without_loop(monkeypatch):
    sent = []

    async def post(msg):
        sent.append(msg)

    monkeypatch.setattr(SN, "post_alert_to_slack", post)
    loop = asyncio.new_event_loop()
    monkeypatch.setattr(asyncio, "get_event_loop", lambda: loop)
    try:
        CA._notify_task_failure(sender=SimpleNamespace(name="app.t"), task_id="1", exception=ValueError("bad"))
    finally:
        loop.close()
    assert "app.t" in sent[0] and "bad" in sent[0]


def test_task_failure_no_loop_runtimeerror(monkeypatch):
    sent = []

    async def post(msg):
        sent.append(msg)

    monkeypatch.setattr(SN, "post_alert_to_slack", post)

    def noloop():
        raise RuntimeError("no current loop")

    monkeypatch.setattr(asyncio, "get_event_loop", noloop)
    CA._notify_task_failure(sender="plain", exception=KeyError("k"))
    assert "plain" in sent[0]


@pytest.mark.asyncio
async def test_task_failure_inside_running_loop(monkeypatch):
    sent = []

    async def post(msg):
        sent.append(msg)

    monkeypatch.setattr(SN, "post_alert_to_slack", post)
    CA._notify_task_failure(sender=SimpleNamespace(name="n"), exception=Exception("e"))
    await asyncio.sleep(0)
    assert sent and "n" in sent[0]


def test_task_failure_swallows_everything(monkeypatch):
    def boom(*a, **k):
        raise TypeError("x")

    monkeypatch.setattr(asyncio, "get_event_loop", boom)
    monkeypatch.setattr(asyncio, "run", boom)
    CA._notify_task_failure(sender=None, exception=None)  # must not raise


# ── OIDC ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_oidc_authorize_url():
    out = await AU.oidc_authorize()
    assert out["url"].startswith(f"{AU.AUTHENTIK_ISSUER}/authorize/?")
    assert "scope=openid+email+profile" in out["url"]


def _db(user):
    db = MagicMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = user
    db.execute = AsyncMock(return_value=res)
    db.add = MagicMock()
    db.commit = AsyncMock()

    async def refresh(u):
        u.id = uuid.uuid4()

    db.refresh = AsyncMock(side_effect=refresh)
    return db


@pytest.mark.asyncio
@respx.mock
async def test_oidc_callback_token_fail():
    respx.post(f"{AU.AUTHENTIK_ISSUER}/token/").mock(return_value=httpx.Response(401, text="nope"))
    with pytest.raises(HTTPException) as e:
        await AU.oidc_callback("c", db=_db(None))
    assert e.value.status_code == 400 and "nope" in e.value.detail


@pytest.mark.asyncio
@respx.mock
async def test_oidc_callback_no_email():
    respx.post(f"{AU.AUTHENTIK_ISSUER}/token/").mock(return_value=httpx.Response(200, json={"access_token": "a"}))
    respx.get(f"{AU.AUTHENTIK_ISSUER}/userinfo/").mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(HTTPException) as e:
        await AU.oidc_callback("c", db=_db(None))
    assert "no email" in e.value.detail


@pytest.mark.asyncio
@respx.mock
async def test_oidc_callback_existing_user():
    respx.post(f"{AU.AUTHENTIK_ISSUER}/token/").mock(return_value=httpx.Response(200, json={"access_token": "a"}))
    ui = respx.get(f"{AU.AUTHENTIK_ISSUER}/userinfo/").mock(return_value=httpx.Response(200, json={"email": "Me@X.gr"}))
    existing = SimpleNamespace(id=uuid.uuid4(), email="me@x.gr")
    out = await AU.oidc_callback("c", db=_db(existing))
    assert out["email"] == "me@x.gr" and out["token_type"] == "bearer" and out["access_token"]
    assert ui.calls[0].request.headers["Authorization"] == "Bearer a"
    # NOTE: the new-user branch builds User(role=...) but User has no `role`
    # column (roles live on TeamMember) -> TypeError in prod; not covered here.


# ── OIDC secret hygiene (2026-11-01: secret moved out of source) ─────────


def test_authentik_config_bound_to_settings():
    """The OIDC config comes from Settings (.env / compose), not literals."""
    from app.core.config import get_settings

    s = get_settings()
    assert AU.AUTHENTIK_ISSUER == s.AUTHENTIK_ISSUER
    assert AU.AUTHENTIK_CLIENT_ID == s.AUTHENTIK_CLIENT_ID
    assert AU.AUTHENTIK_CLIENT_SECRET == s.AUTHENTIK_CLIENT_SECRET


def test_no_hardcoded_authentik_secret_in_source():
    """Regression guard: the client secret must never be a source literal."""
    import inspect
    import re

    src = inspect.getsource(AU)
    assert not re.search(
        r'AUTHENTIK_CLIENT_SECRET\s*=\s*"[0-9a-fA-F]{16,}"', src
    ), "AUTHENTIK_CLIENT_SECRET is hardcoded in auth.py — move it to settings/.env"
