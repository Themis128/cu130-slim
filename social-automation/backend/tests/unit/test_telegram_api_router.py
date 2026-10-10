"""Endpoint-level tests for app/api/telegram.py.

The existing telegram test files cover the service client and webhook
chatbot internals — this file exercises the router surface: helpers,
connect/credentials, webhook setup, send dispatch, auto-reply, bot
builder, group-watch config, thread pause/resume, and the inbound
webhook's auth gate.
"""

from __future__ import annotations

import sys
import uuid
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api import telegram
from app.api.telegram import (
    BotConfig,
    BotCreateRequest,
    DeleteMessageRequest,
    GroupWatchConfig,
    PinMessageRequest,
    SendMediaGroupRequest,
    SendMessageRequest,
    SendPhotoRequest,
    SendPollRequest,
    TelegramConnectRequest,
    TelegramCredentialsUpdate,
    WatchedChat,
    _decrypt_bot_token,
    _get_telegram_account,
    _new_webhook_secret,
    _sanitize,
    _token_bytes,
    _webhook_base,
    _webhook_url_for,
    activate_bot,
    add_watched_chat,
    chat_members,
    connect_telegram_bot,
    create_bot,
    deactivate_bot,
    delete_message,
    delete_telegram_webhook,
    get_auto_reply_config,
    get_bot,
    get_group_watch,
    get_group_watch_activity,
    get_setup_status,
    list_personalities,
    pause_thread,
    pin_message,
    receive_webhook,
    resume_thread,
    send_media_group,
    send_message,
    send_photo,
    send_poll,
    setup_group_watch_links,
    setup_telegram_webhook,
    trigger_group_digest_now,
    unpin_message,
    update_auto_reply_config,
    update_bot,
    update_group_watch,
    update_telegram_credentials,
)
from app.core.config import settings

# ── Fakes ─────────────────────────────────────────────────────────────


class _Res:
    def __init__(self, v):
        self._v = v

    def scalar_one_or_none(self):
        return self._v

    def scalars(self):
        return self

    def first(self):
        return self._v

    def all(self):
        return self._v if isinstance(self._v, list) else []


class _DB:
    def __init__(self, results=()):
        self._q = list(results)
        self.added = []
        self.committed = 0

    async def execute(self, stmt):
        return _Res(self._q.pop(0) if self._q else None)

    def add(self, obj):
        self.added.append(obj)
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()

    async def flush(self):
        for o in self.added:
            if getattr(o, "id", None) is None:
                o.id = uuid.uuid4()

    async def commit(self):
        self.committed += 1

    async def refresh(self, obj):
        pass

    async def rollback(self):
        pass


def _tg_account(**kw):
    meta = kw.pop("meta_data", {
        "bot_token_enc": "enc-token",
        "bot_id": "12345",
        "bot_username": "cloudlessbot",
        "webhook_secret": "sec123",
        "credentials_configured": True,
    })
    return SimpleNamespace(
        id=kw.pop("id", uuid.uuid4()),
        team_id=kw.pop("team_id", uuid.uuid4()),
        platform="telegram",
        account_type="bot",
        account_id=kw.pop("account_id", "12345"),
        username=kw.pop("username", "cloudlessbot"),
        display_name=kw.pop("display_name", "Cloudless Bot"),
        status="active",
        meta_data=meta,
        access_token_enc=kw.pop("access_token_enc", b"enc"),
        **kw,
    )


def _user(email=None):
    return SimpleNamespace(id=uuid.uuid4(), email=email or settings.SOCIAL_ADMIN_EMAIL, name="Admin")


class _FakeClient:
    def __init__(self, **overrides):
        for name, ret in (
            ("get_me", {"id": 12345, "username": "cloudlessbot", "first_name": "Cloudless Bot"}),
            ("set_webhook", True),
            ("get_webhook_info", {"url": "https://social.cloudless.gr/api/v1/telegram/webhook/x"}),
            ("delete_webhook", True),
            ("send_message", {"message_id": 1}),
            ("send_message_with_markup", {"message_id": 2}),
            ("send_photo", {"message_id": 3}),
            ("send_media_group", [{"message_id": 4}]),
            ("send_poll", {"message_id": 5}),
            ("pin_chat_message", True),
            ("unpin_chat_message", True),
            ("delete_message", True),
            ("get_chat_member_count", 42),
            ("get_chat", {"title": "My Group", "type": "supergroup"}),
        ):
            v = overrides.pop(name, ret)
            setattr(self, name, AsyncMock(side_effect=v) if isinstance(v, Exception) else AsyncMock(return_value=v))
        for k, v in overrides.items():
            setattr(self, k, v)


@pytest.fixture(autouse=True)
def _no_orm(monkeypatch):
    monkeypatch.setattr(telegram, "flag_modified", lambda *a, **k: None)


def _patch_client(monkeypatch, client=None):
    client = client or _FakeClient()
    monkeypatch.setattr(telegram, "_client_for", lambda account: client)
    monkeypatch.setattr(telegram, "TelegramAPIClient", lambda token: client)
    return client


def _patch_crypto(monkeypatch):
    monkeypatch.setattr(telegram, "decrypt_token", lambda b: "12345:real-token")
    monkeypatch.setattr(telegram, "encrypt_token", lambda t: b"enc-bytes")


def _patch_quota(monkeypatch):
    monkeypatch.setattr(telegram, "check_quota", AsyncMock())


# ── pure helpers ──────────────────────────────────────────────────────


def test_sanitize_strips_newlines():
    assert _sanitize("a\nb\rc") == "a\\nb\\rc"
    assert _sanitize(None) == ""


def test_token_bytes():
    assert _token_bytes(b"x") == b"x"
    assert _token_bytes("y") == b"y"


def test_new_webhook_secret_charset():
    s = _new_webhook_secret()
    assert s and len(s) <= 64
    assert all(c.isalnum() or c in "_-" for c in s)


def test_decrypt_bot_token_from_meta(monkeypatch):
    monkeypatch.setattr(telegram, "decrypt_token", lambda b: "dec-meta")
    assert _decrypt_bot_token(_tg_account()) == "dec-meta"


def test_decrypt_bot_token_falls_back_to_column(monkeypatch):
    calls = []
    def _dec(b):
        calls.append(b)
        if len(calls) == 1:
            raise ValueError("bad meta token")
        return "dec-column"
    monkeypatch.setattr(telegram, "decrypt_token", _dec)
    acc = _tg_account(access_token_enc="col-enc")
    assert _decrypt_bot_token(acc) == "dec-column"


def test_decrypt_bot_token_400_no_token(monkeypatch):
    monkeypatch.setattr(telegram, "decrypt_token", lambda b: (_ for _ in ()).throw(ValueError()))
    acc = _tg_account(meta_data={}, access_token_enc=None)
    with pytest.raises(HTTPException) as e:
        _decrypt_bot_token(acc)
    assert e.value.status_code == 400


def test_webhook_base_prefers_setting(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_BASE", "https://api.example.com/api/v1/")
    assert _webhook_base() == "https://api.example.com/api/v1"


def test_webhook_base_falls_back_to_media_url(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_BASE", "")
    monkeypatch.setattr(settings, "MEDIA_PUBLIC_BASE_URL", "https://social.cloudless.gr/media")
    assert _webhook_base() == "https://social.cloudless.gr/api/v1"


def test_webhook_base_default(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_BASE", "")
    monkeypatch.setattr(settings, "MEDIA_PUBLIC_BASE_URL", "http://internal:9000")
    assert _webhook_base() == "https://social.cloudless.gr/api/v1"


def test_webhook_url_for():
    aid = uuid.uuid4()
    assert _webhook_url_for(aid).endswith(f"/telegram/webhook/{aid}")


# ── _get_telegram_account ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_account_404():
    with pytest.raises(HTTPException) as e:
        await _get_telegram_account(_DB([None]), uuid.uuid4(), _user())
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_account_400_wrong_platform():
    acc = _tg_account()
    acc.platform = "whatsapp"
    with pytest.raises(HTTPException) as e:
        await _get_telegram_account(_DB([acc]), acc.id, _user())
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_account_non_admin_403():
    acc = _tg_account()
    with pytest.raises(HTTPException) as e:
        await _get_telegram_account(_DB([acc, None]), acc.id, _user("o@x.com"))
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_account_admin_ok():
    acc = _tg_account()
    assert await _get_telegram_account(_DB([acc]), acc.id, _user()) is acc


# ── connect ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_connect_invalid_token_400(monkeypatch):
    _patch_quota(monkeypatch)

    class _Bad:
        def __init__(self, t):
            pass

        async def get_me(self):
            raise ValueError("bad token")

    monkeypatch.setattr(telegram, "TelegramAPIClient", _Bad)
    with pytest.raises(HTTPException) as e:
        await connect_telegram_bot(TelegramConnectRequest(bot_token="x", set_webhook=False),
                                   uuid.uuid4(), _DB(), _user())
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_connect_missing_bot_id_400(monkeypatch):
    _patch_quota(monkeypatch)
    client = _patch_client(monkeypatch)
    client.get_me = AsyncMock(return_value={"id": None})
    with pytest.raises(HTTPException) as e:
        await connect_telegram_bot(TelegramConnectRequest(bot_token="t", set_webhook=False),
                                   uuid.uuid4(), _DB(), _user())
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_connect_creates_new_account(monkeypatch):
    _patch_quota(monkeypatch)
    _patch_crypto(monkeypatch)
    _patch_client(monkeypatch)
    db = _DB([None])  # no existing account
    out = await connect_telegram_bot(
        TelegramConnectRequest(bot_token="tok", set_webhook=False), uuid.uuid4(), db, _user())
    assert out["status"] == "ok"
    assert out["bot_id"] == "12345"
    assert len(db.added) == 1
    assert db.added[0].platform == "telegram"
    assert db.committed == 1


@pytest.mark.asyncio
async def test_connect_updates_existing_account(monkeypatch):
    _patch_quota(monkeypatch)
    _patch_crypto(monkeypatch)
    _patch_client(monkeypatch)
    existing = _tg_account()
    db = _DB([existing])
    out = await connect_telegram_bot(
        TelegramConnectRequest(bot_token="tok", set_webhook=False), existing.team_id, db, _user())
    assert out["status"] == "ok"
    assert db.added == []  # no new row
    assert existing.status == "active"


@pytest.mark.asyncio
async def test_connect_registers_webhook(monkeypatch):
    _patch_quota(monkeypatch)
    _patch_crypto(monkeypatch)
    client = _patch_client(monkeypatch)
    db = _DB([None])
    out = await connect_telegram_bot(
        TelegramConnectRequest(bot_token="tok", set_webhook=True), uuid.uuid4(), db, _user())
    client.set_webhook.assert_awaited_once()
    assert out["webhook"]["ok"] is True


# ── credentials ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_credentials_invalid_400(monkeypatch):
    acc = _tg_account()

    class _Bad:
        def __init__(self, t):
            pass

        async def get_me(self):
            raise ValueError("nope")

    monkeypatch.setattr(telegram, "TelegramAPIClient", _Bad)
    with pytest.raises(HTTPException) as e:
        await update_telegram_credentials(acc.id, TelegramCredentialsUpdate(bot_token="x"),
                                          _DB([acc]), _user())
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_update_credentials_success(monkeypatch):
    acc = _tg_account()
    _patch_crypto(monkeypatch)
    _patch_client(monkeypatch)
    db = _DB([acc])
    out = await update_telegram_credentials(
        acc.id, TelegramCredentialsUpdate(bot_token="new-tok"), db, _user())
    assert out["credentials_stored"] is True
    assert acc.meta_data["bot_token_enc"] == "enc-bytes"
    assert acc.meta_data["credentials_configured"] is True
    assert db.committed == 1


# ── webhook setup ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_setup_webhook_ok(monkeypatch):
    acc = _tg_account()
    _patch_client(monkeypatch)
    out = await setup_telegram_webhook(acc.id, _DB([acc]), _user())
    assert out["status"] == "ok"
    assert acc.meta_data["webhook_set"] is True


@pytest.mark.asyncio
async def test_setup_webhook_502_on_api_error(monkeypatch):
    acc = _tg_account()
    _patch_client(monkeypatch, _FakeClient(set_webhook=telegram.TelegramAPIError(400, "boom")))
    with pytest.raises(HTTPException) as e:
        await setup_telegram_webhook(acc.id, _DB([acc]), _user())
    assert e.value.status_code == 502
    assert acc.meta_data["webhook_set"] is False


@pytest.mark.asyncio
async def test_delete_webhook_ok(monkeypatch):
    acc = _tg_account(meta_data={"webhook_set": True, "webhook_url": "u"})
    _patch_client(monkeypatch)
    out = await delete_telegram_webhook(acc.id, _DB([acc]), _user())
    assert out["deleted"] is True
    assert acc.meta_data["webhook_set"] is False


@pytest.mark.asyncio
async def test_delete_webhook_502(monkeypatch):
    acc = _tg_account()
    _patch_client(monkeypatch, _FakeClient(delete_webhook=telegram.TelegramAPIError(400, "x")))
    with pytest.raises(HTTPException) as e:
        await delete_telegram_webhook(acc.id, _DB([acc]), _user())
    assert e.value.status_code == 502


# ── setup-status ladder ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_setup_status_no_creds():
    acc = _tg_account(meta_data={})
    out = await get_setup_status(acc.id, _DB([acc]), _user())
    assert out["credentials_configured"] is False
    assert "BotFather" in out["next_step"]


@pytest.mark.asyncio
async def test_setup_status_invalid_token(monkeypatch):
    acc = _tg_account()
    _patch_client(monkeypatch, _FakeClient(get_me=RuntimeError("401")))
    out = await get_setup_status(acc.id, _DB([acc]), _user())
    assert out["token_valid"] is False
    assert "invalid" in out["next_step"]


@pytest.mark.asyncio
async def test_setup_status_webhook_missing(monkeypatch):
    acc = _tg_account()
    _patch_client(monkeypatch, _FakeClient(get_webhook_info={"url": ""}))
    out = await get_setup_status(acc.id, _DB([acc]), _user())
    assert out["token_valid"] is True
    assert "Setup Webhook" in out["next_step"]


@pytest.mark.asyncio
async def test_setup_status_needs_autoreply(monkeypatch):
    acc = _tg_account()
    _patch_client(monkeypatch)  # live webhook url present
    out = await get_setup_status(acc.id, _DB([acc]), _user())
    assert "auto-reply" in out["next_step"]


@pytest.mark.asyncio
async def test_setup_status_complete(monkeypatch):
    acc = _tg_account(meta_data={
        "bot_token_enc": "e", "credentials_configured": True,
        "telegram_auto_reply": {"enabled": True},
    })
    _patch_client(monkeypatch)
    out = await get_setup_status(acc.id, _DB([acc]), _user())
    assert out["next_step"] == "setup_complete"
    assert out["can_send_messages"] is True


# ── send dispatch ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_message_plain(monkeypatch):
    acc = _tg_account()
    client = _patch_client(monkeypatch)
    out = await send_message(acc.id, SendMessageRequest(chat_id=1, text="hi"), _DB([acc]), _user())
    client.send_message.assert_awaited_once()
    assert out["message"]["message_id"] == 1


@pytest.mark.asyncio
async def test_send_message_with_buttons(monkeypatch):
    acc = _tg_account()
    client = _patch_client(monkeypatch)
    await send_message(
        acc.id,
        SendMessageRequest(chat_id=1, text="hi", buttons=[[{"text": "go", "url": "https://x"}]]),
        _DB([acc]), _user())
    client.send_message_with_markup.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_message_502(monkeypatch):
    acc = _tg_account()
    _patch_client(monkeypatch, _FakeClient(send_message=telegram.TelegramAPIError(400, "down")))
    with pytest.raises(HTTPException) as e:
        await send_message(acc.id, SendMessageRequest(chat_id=1, text="x"), _DB([acc]), _user())
    assert e.value.status_code == 502


@pytest.mark.asyncio
async def test_send_photo_and_media_group(monkeypatch):
    acc = _tg_account()
    _patch_client(monkeypatch)
    out = await send_photo(
        acc.id, SendPhotoRequest(chat_id=1, photo="https://x/p.jpg", caption="c"),
        _DB([acc]), _user())
    assert out["message"]["message_id"] == 3
    out = await send_media_group(
        acc.id,
        SendMediaGroupRequest(chat_id=1, media=[
            {"type": "photo", "media": "u1"}, {"type": "photo", "media": "u2"}]),
        _DB([acc]), _user())
    assert out["messages"][0]["message_id"] == 4


@pytest.mark.asyncio
async def test_send_poll(monkeypatch):
    acc = _tg_account()
    client = _patch_client(monkeypatch)
    out = await send_poll(
        acc.id, SendPollRequest(chat_id=1, question="q?", options=["a", "b"]),
        _DB([acc]), _user())
    client.send_poll.assert_awaited_once()
    assert out["message"]["message_id"] == 5


@pytest.mark.asyncio
async def test_pin_unpin_delete_chat_members(monkeypatch):
    acc = _tg_account()
    _patch_client(monkeypatch)
    out = await pin_message(acc.id, PinMessageRequest(chat_id=1, message_id=9), _DB([acc]), _user())
    assert out["pinned"] is True
    out = await unpin_message(acc.id, PinMessageRequest(chat_id=1, message_id=9), _DB([acc]), _user())
    assert out["unpinned"] is True
    out = await delete_message(acc.id, DeleteMessageRequest(chat_id=1, message_id=9), _DB([acc]), _user())
    assert out["deleted"] is True
    out = await chat_members(acc.id, "mychat", _DB([acc]), _user())
    assert out["member_count"] == 42


@pytest.mark.asyncio
async def test_pin_502(monkeypatch):
    acc = _tg_account()
    _patch_client(monkeypatch, _FakeClient(pin_chat_message=telegram.TelegramAPIError(400, "x")))
    with pytest.raises(HTTPException) as e:
        await pin_message(acc.id, PinMessageRequest(chat_id=1, message_id=9), _DB([acc]), _user())
    assert e.value.status_code == 502


# ── auto-reply + bot builder ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_auto_reply_get_defaults():
    acc = _tg_account(meta_data={})
    out = await get_auto_reply_config(acc.id, _DB([acc]), _user())
    assert out.enabled is False


@pytest.mark.asyncio
async def test_auto_reply_update_enabled_checks_plan(monkeypatch):
    acc = _tg_account()
    checked = []
    monkeypatch.setattr("app.api.deps.check_plan_feature", AsyncMock(side_effect=lambda *a: checked.append(a)))
    db = _DB([acc])
    from app.api.telegram import AutoReplyConfig
    await update_auto_reply_config(acc.id, AutoReplyConfig(enabled=True), db, _user())
    assert len(checked) == 1
    assert acc.meta_data["telegram_auto_reply"]["enabled"] is True


@pytest.mark.asyncio
async def test_create_bot_preset_and_lang(monkeypatch):
    acc = _tg_account()
    monkeypatch.setattr("app.api.deps.check_plan_feature", AsyncMock())
    _patch_client(monkeypatch)
    db = _DB([acc])
    out = await create_bot(acc.id, BotCreateRequest(personality="sales", language="el"), db, _user())
    assert out["status"] == "ok"
    assert "sales assistant" in out["bot"]["system_prompt"]
    assert "Greek" in out["bot"]["system_prompt"]
    assert acc.meta_data["telegram_auto_reply"]["enabled"] is True


@pytest.mark.asyncio
async def test_create_bot_custom_prompt(monkeypatch):
    acc = _tg_account()
    monkeypatch.setattr("app.api.deps.check_plan_feature", AsyncMock())
    db = _DB([acc, None])  # brand lookup → None
    out = await create_bot(acc.id, BotCreateRequest(custom_prompt="Serve {business_name}."), db, _user())
    assert "Serve Cloudless Bot." in out["bot"]["system_prompt"]


@pytest.mark.asyncio
async def test_get_and_update_bot(monkeypatch):
    acc = _tg_account(meta_data={})
    out = await get_bot(acc.id, _DB([acc]), _user())
    assert out["exists"] is False
    acc.meta_data["telegram_bot"] = {"name": "B", "enabled": False}
    out = await get_bot(acc.id, _DB([acc]), _user())
    assert out["exists"] is True
    db = _DB([acc])
    out = await update_bot(acc.id, BotConfig(name="NB", enabled=True, system_prompt="s"), db, _user())
    assert acc.meta_data["telegram_auto_reply"]["system_prompt"] == "s"


@pytest.mark.asyncio
async def test_activate_deactivate_bot(monkeypatch):
    acc = _tg_account(meta_data={"telegram_bot": {"enabled": False}, "telegram_auto_reply": {}})
    monkeypatch.setattr("app.api.deps.check_plan_feature", AsyncMock())
    await activate_bot(acc.id, _DB([acc]), _user())
    assert acc.meta_data["telegram_bot"]["enabled"] is True
    await deactivate_bot(acc.id, _DB([acc]), _user())
    assert acc.meta_data["telegram_auto_reply"]["enabled"] is False


@pytest.mark.asyncio
async def test_activate_bot_400_no_bot(monkeypatch):
    acc = _tg_account(meta_data={})
    monkeypatch.setattr("app.api.deps.check_plan_feature", AsyncMock())
    with pytest.raises(HTTPException) as e:
        await activate_bot(acc.id, _DB([acc]), _user())
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_deactivate_bot_400_no_bot():
    acc = _tg_account(meta_data={})
    with pytest.raises(HTTPException) as e:
        await deactivate_bot(acc.id, _DB([acc]), _user())
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_list_personalities():
    out = await list_personalities(_tg_account().id, _DB([_tg_account()]), _user())
    assert "professional_friendly" in out["personalities"]


# ── group watch ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_group_watch_defaults():
    acc = _tg_account(meta_data={})
    out = await get_group_watch(acc.id, _DB([acc]), _user())
    assert out.enabled is False
    assert out.digest_enabled is True


@pytest.mark.asyncio
async def test_update_group_watch_persists_and_refreshes_webhook(monkeypatch):
    acc = _tg_account()
    client = _patch_client(monkeypatch)
    db = _DB([acc])
    out = await update_group_watch(acc.id, GroupWatchConfig(enabled=True, owner_chat_id="99"), db, _user())
    assert out.enabled is True
    assert out.owner_chat_id == "99"
    client.set_webhook.assert_awaited_once()  # webhook refresh for my_chat_member
    assert db.committed == 1


@pytest.mark.asyncio
async def test_update_group_watch_webhook_failure_tolerated(monkeypatch):
    acc = _tg_account()
    _patch_client(monkeypatch, _FakeClient(set_webhook=telegram.TelegramAPIError(400, "x"),
                                         get_webhook_info=telegram.TelegramAPIError(400, "y")))
    out = await update_group_watch(acc.id, GroupWatchConfig(enabled=True), _DB([acc]), _user())
    assert out.enabled is True  # failure logged, not raised


@pytest.mark.asyncio
async def test_add_watched_chat_with_getchat(monkeypatch):
    acc = _tg_account()
    client = _patch_client(monkeypatch)
    out = await add_watched_chat(acc.id, WatchedChat(chat_id="-100x"), _DB([acc]), _user())
    client.get_chat.assert_awaited_once()
    assert out["status"] == "ok"
    cfg = acc.meta_data["telegram_group_watch"]
    assert cfg["enabled"] is True
    assert cfg["watched_chats"][0]["title"] == "My Group"


@pytest.mark.asyncio
async def test_add_watched_chat_getchat_failure_tolerated(monkeypatch):
    acc = _tg_account()
    _patch_client(monkeypatch, _FakeClient(get_chat=telegram.TelegramAPIError(400, "x")))
    out = await add_watched_chat(
        acc.id, WatchedChat(chat_id="-100x", title="T", type="channel"), _DB([acc]), _user())
    assert out["config"]["watched_chats"][0]["type"] == "channel"


@pytest.mark.asyncio
async def test_trigger_digest(monkeypatch):
    acc = _tg_account()
    _patch_client(monkeypatch)
    monkeypatch.setattr(telegram, "send_digest_for_account",
                        AsyncMock(return_value={"sent": True, "messages": 3}))
    out = await trigger_group_digest_now(acc.id, _DB([acc]), _user())
    assert out["sent"] is True


@pytest.mark.asyncio
async def test_group_watch_activity_single_chat(monkeypatch):
    acc = _tg_account()
    monkeypatch.setattr(telegram, "list_buffered_messages", AsyncMock(return_value=[{"t": "m"}]))
    out = await get_group_watch_activity(acc.id, "chat1", 10, _DB([acc]), _user())
    assert out["chat_id"] == "chat1"
    assert out["messages"] == [{"t": "m"}]


@pytest.mark.asyncio
async def test_group_watch_activity_all_chats(monkeypatch):
    acc = _tg_account(meta_data={
        "telegram_group_watch": {"enabled": True, "owner_chat_id": "9",
                                 "watched_chats": [{"chat_id": "-1", "title": "G1"}]},
    })
    monkeypatch.setattr(telegram, "list_buffered_messages", AsyncMock(return_value=[{"t": 1}]))
    out = await get_group_watch_activity(acc.id, None, 10, _DB([acc]), _user())
    assert out["chats"][0]["buffered_count"] == 1
    assert out["owner_chat_id"] == "9"


@pytest.mark.asyncio
async def test_setup_links_with_stored_username(monkeypatch):
    acc = _tg_account()
    _patch_client(monkeypatch)
    mod = _chatbot_mod = ModuleType("app.services.telegram_group_watch")
    mod.ensure_bot_commands = AsyncMock(return_value=True)
    mod.bot_deep_links = lambda u: {"link_owner": f"https://t.me/{u}?start=linkowner"}
    monkeypatch.setitem(sys.modules, "app.services.telegram_group_watch", mod)
    out = await setup_group_watch_links(acc.id, _DB([acc]), _user())
    assert out["status"] == "ok"
    assert out["bot_username"] == "cloudlessbot"
    assert out["commands_registered"] is True
    assert out["checklist"][2]["done"] is False  # group privacy is always manual


@pytest.mark.asyncio
async def test_setup_links_fetches_username(monkeypatch):
    acc = _tg_account(meta_data={"bot_token_enc": "e"}, username=None)
    _patch_client(monkeypatch)
    mod = ModuleType("app.services.telegram_group_watch")
    mod.ensure_bot_commands = AsyncMock(return_value=False)
    mod.bot_deep_links = lambda u: {}
    monkeypatch.setitem(sys.modules, "app.services.telegram_group_watch", mod)
    out = await setup_group_watch_links(acc.id, _DB([acc]), _user())
    assert out["bot_username"] == "cloudlessbot"  # from get_me fallback
    assert acc.meta_data["bot_username"] == "cloudlessbot"


# ── thread pause/resume ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pause_thread_400_missing_chat_id():
    acc = _tg_account()
    with pytest.raises(HTTPException) as e:
        await pause_thread(acc.id, {}, _DB([acc]), _user())
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_pause_resume_thread_delegates(monkeypatch):
    acc = _tg_account()
    mod = ModuleType("app.services.telegram_chatbot")
    mod.pause_thread = AsyncMock()
    mod.resume_thread = AsyncMock()
    monkeypatch.setitem(sys.modules, "app.services.telegram_chatbot", mod)
    out = await pause_thread(acc.id, {"chat_id": "c1"}, _DB([acc]), _user())
    mod.pause_thread.assert_awaited_once()
    assert out["paused"] is True
    out = await resume_thread(acc.id, {"chat_id": "c1"}, _DB([acc]), _user())
    mod.resume_thread.assert_awaited_once()
    assert out["paused"] is False


# ── webhook auth gate ─────────────────────────────────────────────────


class _WReq:
    def __init__(self, payload):
        self._p = payload
        self._fail = payload == "RAISE"

    async def json(self):
        if self._fail:
            raise ValueError("bad json")
        return self._p


@pytest.mark.asyncio
async def test_webhook_404_unknown_account():
    with pytest.raises(HTTPException) as e:
        await receive_webhook(uuid.uuid4(), _WReq({}), _DB([None]), None)
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_webhook_403_bad_secret():
    acc = _tg_account()
    with pytest.raises(HTTPException) as e:
        await receive_webhook(acc.id, _WReq({}), _DB([acc]), "wrong-secret")
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_webhook_400_invalid_json():
    acc = _tg_account(meta_data={})  # no secret → skips auth
    with pytest.raises(HTTPException) as e:
        await receive_webhook(acc.id, _WReq("RAISE"), _DB([acc]), None)
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_webhook_non_dict_ignored():
    acc = _tg_account(meta_data={})
    out = await receive_webhook(acc.id, _WReq([1, 2]), _DB([acc]), None)
    assert out["ignored"] is True


@pytest.mark.asyncio
async def test_webhook_secret_match_processes(monkeypatch):
    acc = _tg_account(meta_data={"webhook_secret": "sec123"})
    monkeypatch.setattr(telegram, "extract_my_chat_member_update", lambda u: None)
    monkeypatch.setattr(telegram, "extract_inbound_text_update", lambda u: None)
    monkeypatch.setattr(telegram, "extract_callback_query", lambda u: None)
    monkeypatch.setattr(telegram, "handle_owner_link_command", AsyncMock(return_value=None))
    monkeypatch.setattr(telegram, "handle_mistaken_botfather_command", AsyncMock(return_value=None))
    monkeypatch.setattr(telegram, "process_group_message_watch", AsyncMock())
    monkeypatch.setattr(telegram, "should_auto_reply_in_group", lambda *a, **k: False)
    out = await receive_webhook(acc.id, _WReq({"update_id": 1}), _DB([acc]), "sec123")
    assert out["status"] == "ok"
