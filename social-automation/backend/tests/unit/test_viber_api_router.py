"""Unit tests for app/api/viber.py — Viber bot REST router.

Covers account access control (admin/member/not-found/not-viber),
token decryption fallbacks, connect/credentials upserts, webhook
register/delete/status ladder, send/send-picture/broadcast, auto-reply
config + thread pause/resume, and the HMAC-verified webhook receiver
with its per-event dispatch.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import app.api.viber as V


class _Res:
    def __init__(self, one=None, scalars=None):
        self._one = one

    def scalar_one_or_none(self):
        return self._one

    def scalars(self):
        return self

    def first(self):
        return self._one

    def all(self):
        return []


class _DB:
    def __init__(self, results):
        self._q = list(results)
        self.added = []
        self.commits = 0

    async def execute(self, stmt, *a):
        return self._q.pop(0) if self._q else _Res()

    def add(self, o):
        self.added.append(o)

    async def flush(self):
        pass

    async def commit(self):
        self.commits += 1

    async def refresh(self, o):
        pass


def _account(**kw):
    a = SimpleNamespace(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        platform="viber",
        account_id="bot-1",
        username="cloudless",
        display_name="Cloudless Bot",
        account_type="bot",
        status="active",
        access_token_enc=b"enc",
        meta_data={"viber_auth_token_enc": "enc-tok"},
    )
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def _user(email="admin@x.io"):
    return SimpleNamespace(id=uuid.uuid4(), email=email)


@pytest.fixture
def _admin(monkeypatch):
    monkeypatch.setattr(V.settings, "SOCIAL_ADMIN_EMAIL", "admin@x.io", raising=False)


@pytest.fixture
def _crypto(monkeypatch):
    monkeypatch.setattr(V, "decrypt_token", lambda t: "plain-token")
    monkeypatch.setattr(V, "encrypt_token", lambda t: b"enc-tok")


# ── helpers ──────────────────────────────────────────────────────────


def test_sanitize():
    # sanitize first, then truncate: "a\nb\rc" per unit -> 7 chars
    assert V._sanitize("a\nb\rc" * 200, max_len=10) == "a\\nb\\rca\\n"
    assert V._sanitize("x\ny") == "x\\ny"
    assert V._sanitize(None) == ""


@pytest.mark.asyncio
async def test_get_viber_account_guards(_admin):
    # not found
    with pytest.raises(HTTPException) as ei:
        await V._get_viber_account(_DB([_Res()]), uuid.uuid4(), _user())
    assert ei.value.status_code == 404
    # wrong platform
    with pytest.raises(HTTPException) as ei:
        await V._get_viber_account(_DB([_Res(_account(platform="facebook"))]), uuid.uuid4(), _user())
    assert ei.value.status_code == 400
    # non-admin non-member → 403; member → ok
    acct = _account()
    with pytest.raises(HTTPException) as ei:
        await V._get_viber_account(_DB([_Res(acct), _Res()]), acct.id, _user("u@x.io"))
    assert ei.value.status_code == 403
    out = await V._get_viber_account(_DB([_Res(acct), _Res(SimpleNamespace())]), acct.id, _user("u@x.io"))
    assert out is acct


@pytest.mark.asyncio
async def test_decrypt_auth_token_fallbacks(_crypto, monkeypatch):
    assert V._decrypt_auth_token(_account()) == "plain-token"
    # meta token broken → falls back to access_token_enc
    monkey = _account(meta_data={"viber_auth_token_enc": None})
    assert V._decrypt_auth_token(monkey) == "plain-token"
    # nothing decryptable → 400
    monkeypatch.setattr(V, "decrypt_token", lambda t: (_ for _ in ()).throw(ValueError("x")))
    bad = _account(meta_data={}, access_token_enc=None)
    with pytest.raises(HTTPException) as ei:
        V._decrypt_auth_token(bad)
    assert ei.value.status_code == 400


def test_webhook_base(monkeypatch):
    # settings has no declared fields for these — getattr() lookups,
    # so swap the whole object.
    monkeypatch.setattr(V, "settings", SimpleNamespace(VIBER_WEBHOOK_BASE="https://v.example/api/v1/"))
    assert V._webhook_base() == "https://v.example/api/v1"
    monkeypatch.setattr(V, "settings", SimpleNamespace(VIBER_WEBHOOK_BASE="", MEDIA_PUBLIC_BASE_URL="https://m.example.com/media"))
    assert V._webhook_base() == "https://m.example.com/api/v1"


# ── connect / credentials ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_connect_viber_bot(_admin, _crypto, monkeypatch):
    info = {"id": "bot-9", "name": "My Bot", "uri": "mybot", "subscribers_count": 5, "event_types": ["message"]}
    client = SimpleNamespace(
        get_account_info=AsyncMock(return_value=info),
        set_webhook=AsyncMock(return_value=["message"]),
    )
    monkeypatch.setattr(V, "ViberAPIClient", lambda tok: client)
    monkeypatch.setattr(V, "check_quota", AsyncMock())
    monkeypatch.setattr(V, "flag_modified", lambda *a: None)
    db = _DB([_Res()])  # no existing account
    body = V.ViberConnectRequest(auth_token="tok", set_webhook=True)
    out = await V.connect_viber_bot(body, uuid.uuid4(), db, _user())
    assert out["bot_id"] == "bot-9" and out["webhook"]["ok"] is True
    assert db.added and db.commits == 1
    assert db.added[0].platform == "viber"

    # invalid token → 400
    client.get_account_info = AsyncMock(side_effect=V.ViberAPIError(5, "bad"))
    with pytest.raises(HTTPException) as ei:
        await V.connect_viber_bot(body, uuid.uuid4(), _DB([]), _user())
    assert ei.value.status_code == 400

    # info without id → 400
    client.get_account_info = AsyncMock(return_value={"name": "x"})
    with pytest.raises(HTTPException) as ei:
        await V.connect_viber_bot(body, uuid.uuid4(), _DB([]), _user())
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_connect_existing_account_updates(_admin, _crypto, monkeypatch):
    existing = _account()
    client = SimpleNamespace(
        get_account_info=AsyncMock(return_value={"id": "bot-1", "name": "N", "uri": "u"}),
    )
    monkeypatch.setattr(V, "ViberAPIClient", lambda tok: client)
    monkeypatch.setattr(V, "check_quota", AsyncMock())
    monkeypatch.setattr(V, "flag_modified", lambda *a: None)
    db = _DB([_Res(existing)])
    body = V.ViberConnectRequest(auth_token="tok2", set_webhook=False)
    out = await V.connect_viber_bot(body, uuid.uuid4(), db, _user())
    assert out["status"] == "ok" and not db.added
    assert existing.display_name == "N"
    assert existing.meta_data["credentials_configured"] is True


@pytest.mark.asyncio
async def test_update_credentials(_admin, _crypto, monkeypatch):
    acct = _account()
    client = SimpleNamespace(
        get_account_info=AsyncMock(return_value={"id": "bot-2", "name": "Renamed", "uri": "renamed"}),
        set_webhook=AsyncMock(return_value=["message"]),
    )
    monkeypatch.setattr(V, "ViberAPIClient", lambda tok: client)
    monkeypatch.setattr(V, "flag_modified", lambda *a: None)
    db = _DB([_Res(acct)])
    body = V.ViberCredentialsUpdate(auth_token="new-tok", set_webhook=True)
    out = await V.update_viber_credentials(acct.id, body, db, _user())
    assert out["credentials_stored"] is True
    assert acct.display_name == "Renamed"
    assert out["webhook"]["ok"] is True


# ── webhook management ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_setup_and_delete_webhook(_admin, _crypto, monkeypatch):
    acct = _account()
    client = SimpleNamespace(set_webhook=AsyncMock(return_value=["message"]), unset_webhook=AsyncMock())
    monkeypatch.setattr(V, "ViberAPIClient", lambda tok: client)
    monkeypatch.setattr(V, "flag_modified", lambda *a: None)

    out = await V.setup_viber_webhook(acct.id, _DB([_Res(acct)]), _user())
    assert out["ok"] is True and acct.meta_data["webhook_set"] is True

    # failure → 502 and meta records the error
    client.set_webhook = AsyncMock(side_effect=V.ViberAPIError(5, "refused"))
    with pytest.raises(HTTPException) as ei:
        await V.setup_viber_webhook(acct.id, _DB([_Res(acct)]), _user())
    assert ei.value.status_code == 502
    assert acct.meta_data["webhook_set"] is False

    out = await V.delete_viber_webhook(acct.id, _DB([_Res(acct)]), _user())
    assert out["deleted"] is True
    assert "webhook_url" not in acct.meta_data


@pytest.mark.asyncio
async def test_setup_status_ladder(_admin, _crypto, monkeypatch):
    # no creds → create-bot instruction
    acct = _account(meta_data={})
    out = await V.get_setup_status(acct.id, _DB([_Res(acct)]), _user())
    assert out["credentials_configured"] is False
    assert "Create a Viber bot" in out["next_step"]

    # creds but token invalid → update credentials
    acct = _account()
    client = SimpleNamespace(get_account_info=AsyncMock(side_effect=V.ViberAPIError(5, "x")))
    monkeypatch.setattr(V, "ViberAPIClient", lambda tok: client)
    out = await V.get_setup_status(acct.id, _DB([_Res(acct)]), _user())
    assert "invalid" in out["next_step"]

    # token ok but no https webhook → setup-webhook instruction
    client.get_account_info = AsyncMock(return_value={"webhook": ""})
    monkeypatch.setattr(V, "ViberAPIClient", lambda tok: client)
    out = await V.get_setup_status(acct.id, _DB([_Res(acct)]), _user())
    assert "Setup Webhook" in out["next_step"]

    # everything ok but auto-reply disabled
    client.get_account_info = AsyncMock(return_value={"webhook": "https://x.io/hook"})
    out = await V.get_setup_status(acct.id, _DB([_Res(acct)]), _user())
    assert "auto-reply" in out["next_step"]

    # fully set up
    acct.meta_data["viber_auto_reply"] = {"enabled": True}
    out = await V.get_setup_status(acct.id, _DB([_Res(acct)]), _user())
    assert out["next_step"] == "setup_complete"
    assert out["token_valid"] is True


# ── send / broadcast ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_and_broadcast(_admin, _crypto, monkeypatch):
    acct = _account()
    client = SimpleNamespace(
        send_text=AsyncMock(return_value={"message_token": 42}),
        send_picture=AsyncMock(return_value={"message_token": 43}),
        broadcast_text=AsyncMock(return_value={"status": 0}),
    )
    monkeypatch.setattr(V, "ViberAPIClient", lambda tok: client)

    out = await V.send_message(acct.id, V.SendMessageRequest(receiver="u1", text="hi"), _DB([_Res(acct)]), _user())
    assert out["message_token"] == 42
    assert client.send_text.await_args.kwargs["sender_name"] == "Cloudless Bot"

    out = await V.send_picture(acct.id, V.SendPictureRequest(receiver="u1", media_url="https://x.io/i.png", text="cap"), _DB([_Res(acct)]), _user())
    assert out["message_token"] == 43

    out = await V.broadcast(acct.id, V.BroadcastRequest(broadcast_list=["u1", "u2"], text="b"), _DB([_Res(acct)]), _user())
    assert out["status"] == "ok"

    client.send_text.side_effect = V.ViberAPIError(15, "status 15")
    with pytest.raises(HTTPException) as ei:
        await V.send_message(acct.id, V.SendMessageRequest(receiver="u", text="t"), _DB([_Res(acct)]), _user())
    assert ei.value.status_code == 502


# ── auto-reply / threads ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_auto_reply_config(_admin, monkeypatch):
    acct = _account(meta_data={"viber_auto_reply": {"enabled": False, "fallback_text": "fb"}})
    out = await V.get_auto_reply_config(acct.id, _DB([_Res(acct)]), _user())
    assert out.enabled is False

    monkeypatch.setattr(V, "flag_modified", lambda *a: None)
    import app.api.deps as DEPS

    cpf = AsyncMock()
    monkeypatch.setattr(DEPS, "check_plan_feature", cpf)
    body = V.AutoReplyConfig(enabled=True, fallback_text="fb")
    out = await V.update_auto_reply_config(acct.id, body, _DB([_Res(acct)]), _user())
    assert out.enabled is True
    cpf.assert_awaited_once()  # plan gate runs when enabling
    assert acct.meta_data["viber_auto_reply"]["enabled"] is True


@pytest.mark.asyncio
async def test_thread_pause_resume(_admin, monkeypatch):
    acct = _account()
    import app.services.viber_chatbot as VC

    p = AsyncMock()
    r = AsyncMock()
    monkeypatch.setattr(VC, "pause_thread", p)
    monkeypatch.setattr(VC, "resume_thread", r)
    aid = acct.id
    out = await V.pause_thread(aid, "user-1", _DB([_Res(acct)]), _user())
    assert out["paused"] is True
    assert p.await_args.args == (str(aid), "user-1")
    out = await V.resume_thread(aid, "user-1", _DB([_Res(acct)]), _user())
    assert out["paused"] is False


# ── webhook receiver ─────────────────────────────────────────────────


def _request(body=b'{"event":"webhook"}', json_body=None):
    req = SimpleNamespace()
    req.body = AsyncMock(return_value=body)
    req.json = AsyncMock(return_value=(json_body if json_body is not None else {"event": "webhook"}))
    return req


@pytest.mark.asyncio
async def test_webhook_auth_and_parse(_admin, _crypto, monkeypatch):
    acct = _account()
    # unknown account
    with pytest.raises(HTTPException) as ei:
        await V.receive_webhook(uuid.uuid4(), _request(), _DB([_Res()]))
    assert ei.value.status_code == 404
    # bad signature
    monkeypatch.setattr(V, "verify_signature", lambda b, s, t: False)
    with pytest.raises(HTTPException) as ei:
        await V.receive_webhook(acct.id, _request(), _DB([_Res(acct)]), x_viber_content_signature="bad")
    assert ei.value.status_code == 403
    # bad json
    monkeypatch.setattr(V, "verify_signature", lambda b, s, t: True)
    req = _request()
    req.json = AsyncMock(side_effect=ValueError("x"))
    with pytest.raises(HTTPException) as ei:
        await V.receive_webhook(acct.id, req, _DB([_Res(acct)]))
    assert ei.value.status_code == 400
    # non-dict payload → ignored
    req = _request(json_body=["x"])
    out = await V.receive_webhook(acct.id, req, _DB([_Res(acct)]))
    assert out["ignored"] is True


@pytest.mark.asyncio
async def test_webhook_event_dispatch(_admin, _crypto, monkeypatch):
    acct = _account(meta_data={"viber_auth_token_enc": "t", "subscribers_count": 10, "viber_auto_reply": {"enabled": True, "welcome_message": "welcome!"}})
    monkeypatch.setattr(V, "verify_signature", lambda b, s, t: True)
    monkeypatch.setattr(V, "flag_modified", lambda *a: None)
    client = SimpleNamespace(send_text=AsyncMock())
    monkeypatch.setattr(V, "ViberAPIClient", lambda tok: client)

    # registration ping
    monkeypatch.setattr(V, "parse_webhook_event", lambda p: {"event": "webhook"})
    out = await V.receive_webhook(acct.id, _request(), _DB([_Res(acct)]))
    assert out["event"] == "webhook"

    # subscribed → counter bumps
    monkeypatch.setattr(V, "parse_webhook_event", lambda p: {"event": "subscribed"})
    db = _DB([_Res(acct)])
    await V.receive_webhook(acct.id, _request(), db)
    assert acct.meta_data["subscribers_count"] == 11

    # conversation_started → welcome sent
    monkeypatch.setattr(V, "parse_webhook_event", lambda p: {"event": "conversation_started", "user_id": "u9", "subscribed": False})
    out = await V.receive_webhook(acct.id, _request(), _DB([_Res(acct)]))
    assert out["welcome_sent"] is True
    assert client.send_text.await_args.args[1] == "welcome!"


@pytest.mark.asyncio
async def test_webhook_message_auto_reply(_admin, _crypto, monkeypatch):
    acct = _account(meta_data={"viber_auth_token_enc": "t", "viber_auto_reply": {"enabled": True}})
    monkeypatch.setattr(V, "verify_signature", lambda b, s, t: True)
    client = SimpleNamespace(send_text=AsyncMock())
    monkeypatch.setattr(V, "ViberAPIClient", lambda tok: client)
    import app.services.viber_chatbot as VC

    monkeypatch.setattr(VC, "process_inbound_message", AsyncMock(return_value={"reply": "bot answer", "skipped": False}))
    monkeypatch.setattr(V, "parse_webhook_event", lambda p: {"event": "message", "text": "hello", "user_id": "u9", "user_name": "Bob", "message_token": 1})
    out = await V.receive_webhook(acct.id, _request(), _DB([_Res(acct)]))
    assert out["replied"] is True
    assert client.send_text.await_args.args[1] == "bot answer"

    # bot skipped → reported, nothing sent
    monkeypatch.setattr(VC, "process_inbound_message", AsyncMock(return_value={"skipped": True, "reason": "cooldown"}))
    client.send_text.reset_mock()
    out = await V.receive_webhook(acct.id, _request(), _DB([_Res(acct)]))
    assert out["reason"] == "cooldown"
    client.send_text.assert_not_awaited()

    # auto-reply disabled → flag only
    acct.meta_data["viber_auto_reply"] = {"enabled": False}
    out = await V.receive_webhook(acct.id, _request(), _DB([_Res(acct)]))
    assert out["auto_reply"] is False

    # no text → ignored
    monkeypatch.setattr(V, "parse_webhook_event", lambda p: {"event": "message", "text": "", "user_id": "u9"})
    out = await V.receive_webhook(acct.id, _request(), _DB([_Res(acct)]))
    assert out["ignored"] is True
