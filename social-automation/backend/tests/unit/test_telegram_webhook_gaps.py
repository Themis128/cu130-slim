"""Coverage gaps: app.api.telegram receive_webhook branches + helpers."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.api.telegram as T
from app.services.telegram_api import TelegramAPIError

pytestmark = pytest.mark.asyncio


class _Req:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _Scalar:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row

    def scalar_one(self):
        return self._row


class _DB:
    def __init__(self, rows=()):
        self._rows = list(rows)
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, stmt):
        return self._rows.pop(0) if self._rows else _Scalar(None)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def _db(*rows):
    return _DB([_Scalar(r) for r in rows])


def _account(**meta):
    return SimpleNamespace(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        platform="telegram",
        username="tgbot",
        meta_data=meta or {},
        access_token_enc="enc",
    )


def _client():
    return SimpleNamespace(
        approve_chat_join_request=AsyncMock(return_value=True),
        answer_callback_query=AsyncMock(),
        send_message=AsyncMock(),
        get_me=AsyncMock(return_value={"username": "tgbot"}),
    )


def _patch_common(monkeypatch, account, client=None):
    monkeypatch.setattr(T, "_client_for", lambda acc: client or _client())
    monkeypatch.setattr(T, "_dm_owner", AsyncMock())
    monkeypatch.setattr(T, "_record_channel_event", lambda *a, **k: None)
    monkeypatch.setattr(T, "notify_channel_event", AsyncMock(return_value=True))
    monkeypatch.setattr(T, "flag_modified", lambda *a, **k: None)
    return account


# ── _get_telegram_account auth branches ─────────────────────────────────


async def test_get_account_non_member_403():
    acc = _account()
    user = SimpleNamespace(id=uuid.uuid4(), email="u@example.com")
    db = _db(acc, None)
    with pytest.raises(Exception) as ei:
        await T._get_telegram_account(db, acc.id, user)
    assert getattr(ei.value, "status_code", None) == 403


async def test_get_account_member_ok():
    acc = _account()
    user = SimpleNamespace(id=uuid.uuid4(), email="u@example.com")
    db = _db(acc, SimpleNamespace(id=uuid.uuid4()))
    out = await T._get_telegram_account(db, acc.id, user)
    assert out is acc


# ── auto-reply config disabled skips plan check ─────────────────────────


async def test_auto_reply_disabled_skips_plan_check(monkeypatch):
    acc = _account()
    user = SimpleNamespace(id=uuid.uuid4(), email="u@example.com")
    db = _db(acc, SimpleNamespace(id=uuid.uuid4()))
    monkeypatch.setattr(T, "flag_modified", lambda *a, **k: None)
    body = T.AutoReplyConfig(enabled=False)
    out = await T.update_auto_reply_config(acc.id, body, db=db, user=user)
    assert out.enabled is False
    assert acc.meta_data["telegram_auto_reply"]["enabled"] is False


# ── webhook: join_request branches ──────────────────────────────────────


def _join_req_update():
    return {
        "update_id": 1,
        "chat_join_request": {
            "chat": {"id": -1001, "type": "channel", "title": "Chan"},
            "from": {"id": 42, "username": "joiner", "first_name": "J"},
            "invite_link": {"name": "inv", "invite_link": "https://t.me/+x"},
        },
    }


async def test_join_request_approved(monkeypatch):
    acc = _account()
    db = _db(acc)
    cli = _client()
    _patch_common(monkeypatch, acc, cli)
    monkeypatch.setattr(T, "agent_enabled", lambda a: True)
    monkeypatch.setattr(T, "_agent_cfg", lambda a: {"auto_approve": True})
    out = await T.receive_webhook(acc.id, _Req(_join_req_update()), db=db)
    assert out["join_request"] == "approved"
    cli.approve_chat_join_request.assert_awaited()


async def test_join_request_approve_failed(monkeypatch):
    acc = _account()
    db = _db(acc)
    cli = _client()
    cli.approve_chat_join_request.return_value = False
    _patch_common(monkeypatch, acc, cli)
    monkeypatch.setattr(T, "agent_enabled", lambda a: True)
    monkeypatch.setattr(T, "_agent_cfg", lambda a: {"auto_approve": True})
    out = await T.receive_webhook(acc.id, _Req(_join_req_update()), db=db)
    assert out["join_request"] == "approve_failed"


async def test_join_request_pending_manual(monkeypatch):
    acc = _account()
    db = _db(acc)
    _patch_common(monkeypatch, acc)
    monkeypatch.setattr(T, "agent_enabled", lambda a: False)
    out = await T.receive_webhook(acc.id, _Req(_join_req_update()), db=db)
    assert out["join_request"] == "pending_manual"


async def test_join_request_error(monkeypatch):
    acc = _account()
    db = _db(acc)
    cli = _client()
    cli.approve_chat_join_request.side_effect = RuntimeError("api down")
    _patch_common(monkeypatch, acc, cli)
    monkeypatch.setattr(T, "agent_enabled", lambda a: True)
    monkeypatch.setattr(T, "_agent_cfg", lambda a: {"auto_approve": True})
    out = await T.receive_webhook(acc.id, _Req(_join_req_update()), db=db)
    assert out["join_request"] == "error"


# ── webhook: my_chat_member owner-DM failure ────────────────────────────


def _member_update():
    return {
        "update_id": 2,
        "my_chat_member": {
            "chat": {"id": -1002, "type": "channel", "title": "C2"},
            "from": {"id": 1},
            "old_chat_member": {"status": "left"},
            "new_chat_member": {"status": "administrator"},
        },
    }


async def test_my_chat_member_dm_owner_failure_swallowed(monkeypatch):
    acc = _account()
    db = _db(acc)
    monkeypatch.setattr(T, "_client_for", lambda a: _client())
    monkeypatch.setattr(T, "handle_bot_membership_change", AsyncMock(return_value=({}, {"ok": True})))
    monkeypatch.setattr(T, "notify_channel_event", AsyncMock(return_value=True))
    monkeypatch.setattr(T, "flag_modified", lambda *a, **k: None)
    monkeypatch.setattr(T, "flag_modified", lambda *a, **k: None)
    monkeypatch.setattr(T, "_dm_owner", AsyncMock(side_effect=RuntimeError("dm fail")))
    monkeypatch.setattr(T, "_record_channel_event", lambda *a, **k: None)
    out = await T.receive_webhook(acc.id, _Req(_member_update()), db=db)
    assert out["status"] == "ok" and "membership" in out


# ── webhook: chat_member notify/welcome failures ────────────────────────


def _chat_member_update(new_status="member"):
    return {
        "update_id": 3,
        "chat_member": {
            "chat": {"id": -1003, "type": "supergroup", "title": "G"},
            "old_chat_member": {"status": "left", "user": {"id": 7, "username": "old"}},
            "new_chat_member": {"status": new_status, "user": {"id": 7, "username": "newbie", "first_name": "N"}},
        },
    }


async def test_chat_member_dm_failure_and_welcome_error(monkeypatch):
    acc = _account()
    db = _db(acc)
    monkeypatch.setattr(T, "notify_channel_event", AsyncMock(return_value=True))
    monkeypatch.setattr(T, "flag_modified", lambda *a, **k: None)
    monkeypatch.setattr(T, "_dm_owner", AsyncMock(side_effect=RuntimeError("dm fail")))
    monkeypatch.setattr(T, "maybe_welcome_member", AsyncMock(side_effect=RuntimeError("welcome boom")))
    monkeypatch.setattr(T, "flag_modified", lambda *a, **k: None)
    monkeypatch.setattr(T, "_record_channel_event", lambda *a, **k: None)
    out = await T.receive_webhook(acc.id, _Req(_chat_member_update()), db=db)
    assert out["member_event"] == "member_joined"
    assert out["welcomed"] is False


async def test_chat_member_left_no_welcome(monkeypatch):
    acc = _account()
    db = _db(acc)
    monkeypatch.setattr(T, "notify_channel_event", AsyncMock(return_value=True))
    monkeypatch.setattr(T, "flag_modified", lambda *a, **k: None)
    monkeypatch.setattr(T, "_dm_owner", AsyncMock())
    monkeypatch.setattr(T, "maybe_welcome_member", AsyncMock())
    monkeypatch.setattr(T, "_record_channel_event", lambda *a, **k: None)
    out = await T.receive_webhook(acc.id, _Req(_chat_member_update(new_status="left")), db=db)
    assert out["member_event"] == "member_left"
    assert "welcomed" not in out


# ── webhook: callback_query LEAD_* paths ────────────────────────────────


def _callback_update(data="LEAD_START"):
    return {
        "update_id": 4,
        "callback_query": {
            "id": "cb1",
            "data": data,
            "from": {"id": 9},
            "message": {"message_id": 5, "chat": {"id": 555, "type": "private"}},
        },
    }


async def test_callback_lead_capture_completed(monkeypatch):
    acc = _account()
    db = _db(acc)
    cli = _client()
    _patch_common(monkeypatch, acc, cli)
    monkeypatch.setattr(T, "_lead_enabled_and_active", AsyncMock(return_value=True))
    monkeypatch.setattr(T, "_run_lead_capture", AsyncMock(return_value=SimpleNamespace(completed_lead_id=uuid.uuid4())))
    monkeypatch.setattr(T, "_deliver_lead_reply", AsyncMock())
    import app.services.lead_capture as LC

    monkeypatch.setattr(LC, "snapshot_lead_session", AsyncMock(return_value=None))
    out = await T.receive_webhook(acc.id, _Req(_callback_update()), db=db)
    assert out["lead_capture"] is True and "lead_id" in out


async def test_callback_lead_capture_no_id_and_none_reply(monkeypatch):
    acc = _account()
    db = _db(acc)
    cli = _client()
    _patch_common(monkeypatch, acc, cli)
    monkeypatch.setattr(T, "_lead_enabled_and_active", AsyncMock(return_value=True))
    monkeypatch.setattr(T, "_run_lead_capture", AsyncMock(return_value=SimpleNamespace(completed_lead_id=None)))
    monkeypatch.setattr(T, "_deliver_lead_reply", AsyncMock())
    import app.services.lead_capture as LC

    monkeypatch.setattr(LC, "snapshot_lead_session", AsyncMock(return_value=None))
    out = await T.receive_webhook(acc.id, _Req(_callback_update()), db=db)
    assert out["lead_capture"] is True and "lead_id" not in out

    # lead_reply falsy -> skips deliver entirely
    monkeypatch.setattr(T, "_run_lead_capture", AsyncMock(return_value=None))
    db._rows.append(_Scalar(acc))
    out2 = await T.receive_webhook(acc.id, _Req(_callback_update()), db=db)
    assert "lead_capture" not in out2 and out2["callback_answered"] is True


async def test_callback_lead_inactive_and_error(monkeypatch):
    acc = _account()
    db = _db(acc)
    cli = _client()
    _patch_common(monkeypatch, acc, cli)
    # lead capture disabled -> plain ack
    monkeypatch.setattr(T, "_lead_enabled_and_active", AsyncMock(return_value=False))
    out = await T.receive_webhook(acc.id, _Req(_callback_update()), db=db)
    assert out["callback_answered"] is True and "lead_capture" not in out

    # exception inside callback handling -> callback_error
    monkeypatch.setattr(T, "_lead_enabled_and_active", AsyncMock(side_effect=RuntimeError("boom")))
    db._rows.append(_Scalar(acc))
    out2 = await T.receive_webhook(acc.id, _Req(_callback_update()), db=db)
    assert out2["callback_error"] is True


# ── webhook: group auto-reply allowed + private lead paths ──────────────


def _msg_update(chat_type="group", text="hi @tgbot"):
    return {
        "update_id": 5,
        "message": {
            "message_id": 11,
            "chat": {"id": -200, "type": chat_type, "title": "Grp"},
            "from": {"id": 3, "first_name": "U"},
            "text": text,
        },
    }


async def test_group_message_auto_reply_allowed_falls_through(monkeypatch):
    acc = _account()
    db = _db(acc)
    cli = _client()
    _patch_common(monkeypatch, acc, cli)
    monkeypatch.setattr(T, "handle_mistaken_botfather_command", AsyncMock(return_value=False))
    monkeypatch.setattr(T, "handle_owner_link_command", AsyncMock(return_value=({}, False)))
    monkeypatch.setattr(T, "process_group_message_watch", AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr(T, "should_auto_reply_in_group", lambda *a, **k: True)
    monkeypatch.setattr(T, "_lead_enabled_and_active", AsyncMock(return_value=False))
    out = await T.receive_webhook(acc.id, _Req(_msg_update()), db=db)
    assert out["auto_reply"] is False  # meta auto_reply disabled


async def test_private_dm_lead_capture_paths(monkeypatch):
    acc = _account()
    db = _db(acc)
    cli = _client()
    _patch_common(monkeypatch, acc, cli)
    monkeypatch.setattr(T, "handle_mistaken_botfather_command", AsyncMock(return_value=False))
    monkeypatch.setattr(T, "handle_owner_link_command", AsyncMock(return_value=({}, False)))
    monkeypatch.setattr(T, "_lead_enabled_and_active", AsyncMock(return_value=True))
    import app.services.lead_capture as LC

    monkeypatch.setattr(LC, "snapshot_lead_session", AsyncMock(return_value=None))

    # lead_reply None -> falls through to auto_reply disabled
    monkeypatch.setattr(T, "_run_lead_capture", AsyncMock(return_value=None))
    upd = _msg_update(chat_type="private", text="hello")
    upd["message"]["chat"] = {"id": 777, "type": "private"}
    out = await T.receive_webhook(acc.id, _Req(upd), db=db)
    assert out.get("auto_reply") is False and "lead_capture" not in out

    # lead_reply with completed id -> early return with lead_id
    monkeypatch.setattr(T, "_run_lead_capture", AsyncMock(return_value=SimpleNamespace(completed_lead_id=uuid.uuid4())))
    monkeypatch.setattr(T, "_deliver_lead_reply", AsyncMock())
    db._rows.append(_Scalar(acc))
    out2 = await T.receive_webhook(acc.id, _Req(upd), db=db)
    assert out2["lead_capture"] is True and "lead_id" in out2

    # TelegramAPIError from deliver -> lead_send_failed
    monkeypatch.setattr(T, "_deliver_lead_reply", AsyncMock(side_effect=TelegramAPIError(500, "send fail")))
    db._rows.append(_Scalar(acc))
    out3 = await T.receive_webhook(acc.id, _Req(upd), db=db)
    assert out3["lead_send_failed"] is True


async def test_private_dm_lead_generic_exception_continues(monkeypatch):
    acc = _account()
    db = _db(acc)
    _patch_common(monkeypatch, acc, _client())
    monkeypatch.setattr(T, "handle_mistaken_botfather_command", AsyncMock(return_value=False))
    monkeypatch.setattr(T, "handle_owner_link_command", AsyncMock(return_value=({}, False)))
    monkeypatch.setattr(T, "_lead_enabled_and_active", AsyncMock(return_value=True))
    monkeypatch.setattr(T, "_run_lead_capture", AsyncMock(side_effect=RuntimeError("fsm boom")))
    upd = _msg_update(chat_type="private", text="hi")
    upd["message"]["chat"] = {"id": 888, "type": "private"}
    out = await T.receive_webhook(acc.id, _Req(upd), db=db)
    assert out.get("auto_reply") is False


async def test_run_lead_capture_invokes_fsm(monkeypatch):
    acc = _account()
    import app.services.lead_capture as LC

    seen = {}

    async def _fsm(db, **kw):
        seen.update(kw)
        return "REPLY"

    monkeypatch.setattr(LC, "handle_lead_capture_message", _fsm)
    out = await T._run_lead_capture(
        _db(), acc, {"chat_id": 42, "chat_type": "private", "text": "hi", "chat_title": "T"}, payload="LEAD_X"
    )
    assert out == "REPLY"
    assert seen["thread_id"] == "42" and seen["postback_payload"] == "LEAD_X"


async def test_setup_links_no_username_from_get_me(monkeypatch):
    acc = _account()
    acc.username = ""  # force the get_me lookup; empty result -> no meta write
    user = SimpleNamespace(id=uuid.uuid4(), email="u@example.com")
    db = _db(acc, SimpleNamespace(id=uuid.uuid4()))
    monkeypatch.setattr(T, "flag_modified", lambda *a, **k: None)
    cli = _client()
    cli.get_me = AsyncMock(return_value={})  # no username -> skip meta write
    monkeypatch.setattr(T, "_client_for", lambda a: cli)
    monkeypatch.setattr("app.services.telegram_group_watch.ensure_bot_commands", AsyncMock(return_value=True))
    monkeypatch.setattr(T, "_register_webhook", AsyncMock(return_value={}))
    out = await T.setup_group_watch_links(acc.id, db=db, user=user)
    assert out["status"] == "ok"


async def test_join_request_dm_owner_failure_swallowed(monkeypatch):
    acc = _account()
    db = _db(acc)
    cli = _client()
    _patch_common(monkeypatch, acc, cli)
    monkeypatch.setattr(T, "_dm_owner", AsyncMock(side_effect=RuntimeError("dm fail")))
    monkeypatch.setattr(T, "agent_enabled", lambda a: True)
    monkeypatch.setattr(T, "_agent_cfg", lambda a: {"auto_approve": True})
    out = await T.receive_webhook(acc.id, _Req(_join_req_update()), db=db)
    assert out["join_request"] == "approved"
