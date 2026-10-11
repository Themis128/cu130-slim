"""Unit tests for app/worker/tasks/instagram_messenger.py — IG DM bot poller.

Covers the Meta rate-limit detector, human-handoff Slack/email notify,
the account loop (disabled/no-token/error paths), and _process_account
Graph → browser-bridge fallback chain with the full conversation pipeline
(seen-dedup, cooldown, pause, frustration handoff, lead capture, echo
guard, send/mark-read/memory bookkeeping).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

import app.worker.tasks.instagram_messenger as IG


def _exc(status=400, text=""):
    return IG.InstagramAPIError(status, text, "http://x")


def test_is_app_rate_limit():
    assert IG._is_app_rate_limit(_exc(429)) is False  # needs matching text too
    assert IG._is_app_rate_limit(_exc(429, "request limit reached")) is True
    assert IG._is_app_rate_limit(_exc(400, '{"code": 4}')) is True
    assert IG._is_app_rate_limit(_exc(403, '"error_subcode": 1349210')) is True
    assert IG._is_app_rate_limit(_exc(400, "request limit reached")) is True
    assert IG._is_app_rate_limit(_exc(401, "unauthorized")) is False
    assert IG._is_app_rate_limit(_exc(500, "code: 4")) is False  # wrong status


# ── _notify_human_handoff ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handoff_notify(monkeypatch):
    account = SimpleNamespace(id=uuid4(), display_name="IG Acct")
    slack = AsyncMock()
    import app.services.slack_notifications as SN

    monkeypatch.setattr(SN, "_post_slack_text", slack)
    import sys

    email = AsyncMock()
    monkeypatch.setitem(sys.modules, "app.services.email_digest", SimpleNamespace(send_email=email))
    s = IG.get_settings()
    monkeypatch.setattr(s, "SLACK_LEADS_WEBHOOK_URL", "http://hook", raising=False)
    monkeypatch.setattr(s, "DIGEST_EMAIL_TO", "ops@x.io", raising=False)
    await IG._notify_human_handoff(account, "bob", "thread-xyz", "stop it")
    assert slack.await_args.kwargs["purpose"] == "dm-handoff"
    assert "bob" in slack.await_args.kwargs["text"]
    email.assert_awaited_once()

    # nothing configured → returns silently
    monkeypatch.setattr(s, "SLACK_LEADS_WEBHOOK_URL", "", raising=False)
    monkeypatch.setattr(s, "SLACK_WEBHOOK_URL", "", raising=False)
    monkeypatch.setattr(s, "SLACK_BOT_TOKEN", "", raising=False)
    monkeypatch.setattr(s, "SLACK_ACCESS_TOKEN", "", raising=False)
    monkeypatch.setattr(s, "SLACK_CHANNEL_ID", "", raising=False)
    monkeypatch.setattr(s, "SLACK_LEADS_CHANNEL_ID", "", raising=False)
    slack.reset_mock()
    await IG._notify_human_handoff(account, "b", "t", "x")
    slack.assert_not_awaited()


# ── _poll_instagram_messenger_async account loop ──────────────────────


class _SessionCtx:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *a):
        return False


class _Res:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return self

    def all(self):
        return self._items


def _account(**kw):
    a = SimpleNamespace(
        id=uuid4(),
        team_id=uuid4(),
        platform="instagram",
        status="active",
        username="cloudless.gr",
        display_name="Cloudless",
        account_id="ig-1",
        access_token_enc="enc",
        meta_data={"instagram_auto_reply": {"enabled": True}},
    )
    for k, v in kw.items():
        setattr(a, k, v)
    return a


@pytest.mark.asyncio
async def test_poll_account_loop(monkeypatch):
    good = _account()
    disabled = _account(meta_data={"instagram_auto_reply": {"enabled": False}})
    no_token = _account(access_token_enc=None)
    db = SimpleNamespace(execute=AsyncMock(return_value=_Res([good, disabled, no_token])), commit=AsyncMock())
    monkeypatch.setattr(IG, "_worker_db", lambda: _SessionCtx(db))
    monkeypatch.setattr(IG, "flag_modified", lambda *a: None, raising=False)
    proc = AsyncMock(return_value=2)
    monkeypatch.setattr(IG, "_process_account", proc)
    import sqlalchemy.orm.attributes as SOA

    monkeypatch.setattr(SOA, "flag_modified", lambda *a: None)

    out = await IG._poll_instagram_messenger_async()
    assert out == {"accounts_checked": 1, "replies_sent": 2, "errors": 0, "skipped_no_token": 1}
    db.commit.assert_awaited_once()

    # _process_account error → counted, loop continues
    proc.side_effect = RuntimeError("boom")
    out = await IG._poll_instagram_messenger_async()
    assert out["errors"] == 1


# ── _process_account ──────────────────────────────────────────────────


def _bot_patches(monkeypatch, **kw):
    """Patch every bot-helper seam; kwargs tweak returns."""
    monkeypatch.setattr(IG, "is_instagram_app_rate_limited", AsyncMock(return_value=kw.get("app_limited", False)))
    monkeypatch.setattr(IG, "check_cooldown", AsyncMock(return_value=kw.get("cooldown_ok", True)))
    monkeypatch.setattr(IG, "is_thread_paused", AsyncMock(return_value=kw.get("paused", False)))
    monkeypatch.setattr(IG, "set_cooldown", AsyncMock())
    monkeypatch.setattr(IG, "pause_thread", AsyncMock())
    monkeypatch.setattr(IG, "set_instagram_app_rate_limited", AsyncMock())
    monkeypatch.setattr(IG, "is_frustrated_message", Mock(return_value=kw.get("frustrated", False)))
    monkeypatch.setattr(IG, "detect_intent", AsyncMock(return_value="question"))
    monkeypatch.setattr(IG, "retrieve_brand_context", AsyncMock(return_value=""))
    monkeypatch.setattr(IG, "generate_contextual_reply", AsyncMock(return_value=kw.get("reply", "the reply")))
    monkeypatch.setattr(IG, "store_message_memory", AsyncMock())
    monkeypatch.setattr(IG, "_notify_human_handoff", AsyncMock())
    monkeypatch.setattr(IG, "decrypt_token", lambda e: "tok")
    import asyncio

    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    import app.services.lead_capture as LC

    monkeypatch.setattr(LC, "handle_lead_capture_message", AsyncMock(return_value=kw.get("lead_reply")))


def _convo(cid="c1", pid="user-9", name="bob", last_msg=None):
    embedded = []
    if last_msg is not None:
        embedded = [{"from": {"id": pid}, "message": last_msg}]
    return {
        "id": cid,
        "participants": {"data": [{"id": pid, "username": name}]},
        "messages": {"data": embedded},
    }


def _graph_client(conversations=None, messages=None):
    c = SimpleNamespace()
    c.get_conversations = AsyncMock(return_value={"data": conversations or []})
    c.get_me = AsyncMock(return_value={"user_id": "ig-1"})
    c.get_dm_messages = AsyncMock(return_value={"data": messages or []})
    c.send_dm = AsyncMock()
    c.send_typing_indicator = AsyncMock()
    c.mark_dm_read = AsyncMock()
    return c


@pytest.mark.asyncio
async def test_process_account_graph_happy(monkeypatch):
    _bot_patches(monkeypatch)
    inbound = [{"from": {"id": "user-9"}, "message": "how much is it?"}]
    client = _graph_client(conversations=[_convo(last_msg="how much is it?")], messages=inbound)
    monkeypatch.setattr(IG, "InstagramAPIClient", lambda **kw: client)
    account = _account()
    n = await IG._process_account(SimpleNamespace(), account, {"enabled": True}, {}, "", "", "http://dmr")
    assert n == 1
    client.send_dm.assert_awaited_once()
    assert client.send_dm.await_args.args[0] == "user-9"
    IG.set_cooldown.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_account_seen_and_own_message_skip(monkeypatch):
    _bot_patches(monkeypatch)
    # latest embedded message is ours → skip without fetching messages
    client = _graph_client(conversations=[_convo(last_msg="hi")])
    # last_msg from pid but seen already has same text → skip
    convos = [_convo(last_msg="hello")]
    client.get_conversations = AsyncMock(return_value={"data": convos})
    monkeypatch.setattr(IG, "InstagramAPIClient", lambda **kw: client)
    account = _account()
    n = await IG._process_account(SimpleNamespace(), account, {"enabled": True}, {"c1": "hello"}, "", "", "http://dmr")
    assert n == 0
    client.get_dm_messages.assert_not_awaited()

    # embedded message from us (from.id in my_ids) → skipped entirely
    mine = _convo(last_msg=None)
    mine["messages"]["data"] = [{"from": {"id": "ig-1"}, "message": "out"}]
    client.get_conversations = AsyncMock(return_value={"data": [mine]})
    n = await IG._process_account(SimpleNamespace(), account, {"enabled": True}, {}, "", "", "http://dmr")
    assert n == 0


@pytest.mark.asyncio
async def test_process_account_frustration_handoff(monkeypatch):
    _bot_patches(monkeypatch, frustrated=True)
    inbound = [{"from": {"id": "user-9"}, "message": "stop messaging"}]
    client = _graph_client(conversations=[_convo(last_msg="stop messaging")], messages=inbound)
    monkeypatch.setattr(IG, "InstagramAPIClient", lambda **kw: client)
    n = await IG._process_account(SimpleNamespace(), _account(), {"enabled": True}, {}, "", "", "http://dmr")
    assert n == 0
    IG.pause_thread.assert_awaited_once()
    IG._notify_human_handoff.assert_awaited_once()
    client.send_dm.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_account_cooldown_paused(monkeypatch):
    _bot_patches(monkeypatch, cooldown_ok=False)
    client = _graph_client(conversations=[_convo(last_msg="new msg")], messages=[{"from": {"id": "user-9"}, "message": "new msg"}])
    monkeypatch.setattr(IG, "InstagramAPIClient", lambda **kw: client)
    n = await IG._process_account(SimpleNamespace(), _account(), {"enabled": True}, {}, "", "", "http://dmr")
    assert n == 0
    client.send_dm.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_account_echo_guard_and_send_fail(monkeypatch):
    _bot_patches(monkeypatch, reply="hello back")  # echoes inbound text
    client = _graph_client(conversations=[_convo(last_msg="hello back")], messages=[{"from": {"id": "user-9"}, "message": "hello back"}])
    monkeypatch.setattr(IG, "InstagramAPIClient", lambda **kw: client)
    account = _account()
    n = await IG._process_account(SimpleNamespace(), account, {"enabled": True, "fallback_text": "FALLBACK"}, {}, "", "", "http://dmr")
    # echo detected → fallback text sent instead
    assert n == 1
    assert client.send_dm.await_args.args[1] == "FALLBACK"

    # send failure → not marked seen, not counted
    _bot_patches(monkeypatch)
    client.send_dm.side_effect = RuntimeError("send fail")
    n = await IG._process_account(SimpleNamespace(), account, {"enabled": True}, {}, "", "", "http://dmr")
    assert n == 0


@pytest.mark.asyncio
async def test_process_account_rate_limit_bridge_fallback(monkeypatch):
    _bot_patches(monkeypatch)
    # Graph client probe raises rate limit → breaker set → bridge path
    bad_client = _graph_client()
    bad_client.get_conversations.side_effect = _exc(429, "request limit reached")
    monkeypatch.setattr(IG, "InstagramAPIClient", lambda **kw: bad_client)

    bridge = SimpleNamespace(
        ensure_session=AsyncMock(),
        get_instagram_dm_conversations=AsyncMock(return_value={"conversations": [{"id": "bc1", "participant_id": "u9", "name": "bob"}]}),
        get_instagram_dm_messages=AsyncMock(return_value={"messages": [{"is_sent_by_viewer": False, "text": "hi"}]}),
        send_instagram_dm_message=AsyncMock(return_value={"ok": True}),
    )
    monkeypatch.setattr(IG, "BrowserBridgeClient", lambda **kw: bridge)

    class _BSession:
        async def __aenter__(self):
            return bridge

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(IG, "browser_session", lambda *a, **kw: _BSession())
    n = await IG._process_account(SimpleNamespace(), _account(), {"enabled": True}, {}, "", "", "http://dmr")
    IG.set_instagram_app_rate_limited.assert_awaited()
    assert n == 1
    bridge.send_instagram_dm_message.assert_awaited()


@pytest.mark.asyncio
async def test_process_account_bridge_unavailable(monkeypatch):
    _bot_patches(monkeypatch, app_limited=True)
    bridge = SimpleNamespace(ensure_session=AsyncMock(side_effect=RuntimeError("no session")))
    monkeypatch.setattr(IG, "BrowserBridgeClient", lambda **kw: bridge)
    n = await IG._process_account(SimpleNamespace(), _account(), {"enabled": True}, {}, "", "", "http://dmr")
    assert n == 0


@pytest.mark.asyncio
async def test_process_account_lead_capture(monkeypatch):
    lead = SimpleNamespace(text="qualifying question?")
    _bot_patches(monkeypatch, lead_reply=lead)
    client = _graph_client(conversations=[_convo(last_msg="i want a website")], messages=[{"from": {"id": "user-9"}, "message": "i want a website"}])
    monkeypatch.setattr(IG, "InstagramAPIClient", lambda **kw: client)
    n = await IG._process_account(SimpleNamespace(), _account(), {"enabled": True}, {}, "", "", "http://dmr")
    assert n == 1
    # lead reply sent via Graph, not the generic AI reply
    assert client.send_dm.await_args.args[1] == "qualifying question?"
