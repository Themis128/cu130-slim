"""Unit tests for app/worker/tasks/instagram_messenger.py.

Covers the Instagram DM auto-reply poller: rate-limit detection, the
sync/async runner, the poll loop, human-handoff notification, and the
per-account Graph-API / browser-bridge processing paths (conversation
fetch, inbound collection, seen/cooldown/paused guards, frustration
handoff, lead capture, echo guard, send and error handling).
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import app.worker.tasks.instagram_messenger as IM


def _account(meta: dict, **kw):
    return SimpleNamespace(
        id=kw.get("id", uuid.uuid4()),
        team_id=kw.get("team_id", uuid.uuid4()),
        display_name=kw.get("display_name", "Cloudless"),
        username=kw.get("username", "cloudless.gr"),
        account_id=kw.get("account_id", "acc-ig-1"),
        access_token_enc=kw.get("access_token_enc", "enc-tok"),
        meta_data=meta,
    )


def _settings():
    return SimpleNamespace(
        CLOUDFLARE_API_TOKEN="",
        CLOUDFLARE_ACCOUNT_ID="",
        DMR_BASE_URL="http://dmr",
        BROWSER_BRIDGE_URL="http://bridge",
        SLACK_LEADS_WEBHOOK_URL="",
        SLACK_WEBHOOK_URL="",
        SLACK_BOT_TOKEN="",
        SLACK_ACCESS_TOKEN="",
        SLACK_LEADS_CHANNEL_ID="",
        SLACK_CHANNEL_ID="",
        DIGEST_EMAIL_TO="",
    )


def _wire(monkeypatch, *, rate_limited=False):
    """Patch all shared external seams of the poller."""
    monkeypatch.setattr(IM, "is_instagram_app_rate_limited", AsyncMock(return_value=rate_limited))
    monkeypatch.setattr(IM, "set_instagram_app_rate_limited", AsyncMock())
    monkeypatch.setattr(IM, "decrypt_token", lambda t: "tok")
    monkeypatch.setattr(IM, "check_cooldown", AsyncMock(return_value=True))
    monkeypatch.setattr(IM, "set_cooldown", AsyncMock())
    monkeypatch.setattr(IM, "is_thread_paused", AsyncMock(return_value=False))
    monkeypatch.setattr(IM, "pause_thread", AsyncMock())
    monkeypatch.setattr(IM, "is_frustrated_message", lambda t: False)
    monkeypatch.setattr(IM, "detect_intent", AsyncMock(return_value="i"))
    monkeypatch.setattr(IM, "retrieve_brand_context", AsyncMock(return_value="ctx"))
    monkeypatch.setattr(IM, "generate_contextual_reply", AsyncMock(return_value="reply"))
    monkeypatch.setattr(IM, "store_message_memory", AsyncMock())
    monkeypatch.setattr(IM, "_notify_human_handoff", AsyncMock())
    monkeypatch.setattr(IM, "get_settings", _settings)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())


def _graph_client(convos=None, msgs=None, me=None):
    return SimpleNamespace(
        get_conversations=AsyncMock(return_value=convos or {"data": []}),
        get_dm_messages=AsyncMock(return_value=msgs or {"data": []}),
        get_me=AsyncMock(return_value=me or {"user_id": "me-igsid"}),
        send_dm=AsyncMock(return_value={}),
        mark_dm_read=AsyncMock(return_value={}),
        send_typing_indicator=AsyncMock(return_value={}),
    )


def _bridge(convos=None, msgs=None, send_result=None):
    return SimpleNamespace(
        ensure_session=AsyncMock(return_value={"status": "active"}),
        get_instagram_dm_conversations=AsyncMock(return_value=convos or {"conversations": []}),
        get_instagram_dm_messages=AsyncMock(return_value=msgs or {"messages": []}),
        send_instagram_dm_message=AsyncMock(return_value=send_result if send_result is not None else {}),
    )


def _wire_bridge(monkeypatch, bridge):
    monkeypatch.setattr(IM, "BrowserBridgeClient", lambda *a, **k: bridge)

    class _BS:
        async def __aenter__(self):
            return bridge

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(IM, "browser_session", lambda *a, **k: _BS())


def _convo(cid="c1", participant="u1", name="Ada", embedded=None):
    c = {
        "id": cid,
        "participants": {"data": [{"id": participant, "username": name}]},
    }
    if embedded is not None:
        c["messages"] = {"data": embedded}
    return c


def _err(status=400, text="oops"):
    return IM.InstagramAPIError(status, text, "http://x")


# ── rate-limit detector ──────────────────────────────────────────────


def test_is_app_rate_limit():
    assert IM._is_app_rate_limit(_err(429, "request limit reached")) is True
    assert IM._is_app_rate_limit(_err(400, '{"code": 4}')) is True
    assert IM._is_app_rate_limit(_err(403, '{"code": 32}')) is True
    assert IM._is_app_rate_limit(_err(400, '{"code": 613}')) is True
    assert IM._is_app_rate_limit(_err(400, '{"error_subcode": 1349210}')) is True
    assert IM._is_app_rate_limit(_err(400, "bad request")) is False
    assert IM._is_app_rate_limit(_err(500, "request limit reached")) is False


# ── _run_async ───────────────────────────────────────────────────────


def test_run_async_sync_path():
    async def _coro():
        return 42

    assert IM._run_async(_coro()) == 42


def test_run_async_no_event_loop(monkeypatch):
    monkeypatch.setattr(asyncio, "get_event_loop", Mock(side_effect=RuntimeError("no loop")))
    monkeypatch.setattr(IM, "run_async", Mock(return_value="ran"))

    async def _coro():
        return 0

    coro = _coro()
    assert IM._run_async(coro) == "ran"
    coro.close()  # avoid unawaited-coroutine warning


def test_poll_task_wrapper(monkeypatch):
    def _fake(coro):
        coro.close()  # avoid unawaited-coroutine warning
        return {"ok": 1}

    monkeypatch.setattr(IM, "_run_async", _fake)
    assert IM.poll_instagram_messenger() == {"ok": 1}


@pytest.mark.asyncio
async def test_run_async_inside_loop():
    async def _coro():
        return 7

    # running loop → spawned thread with its own loop
    assert IM._run_async(_coro()) == 7

    async def _boom():
        raise ValueError("inner")

    with pytest.raises(ValueError, match="inner"):
        IM._run_async(_boom())


# ── poller ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_poller(monkeypatch):
    class _DB:
        def __init__(self, accounts):
            self.accounts = accounts
            self.commit = AsyncMock()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, *a):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: self.accounts))

    monkeypatch.setattr(IM, "get_settings", _settings)
    monkeypatch.setattr(IM, "_worker_db", lambda: _DB([]))
    out = await IM._poll_instagram_messenger_async()
    assert out == {"accounts_checked": 0, "replies_sent": 0, "errors": 0, "skipped_no_token": 0}

    off = _account({"instagram_auto_reply": {"enabled": False}})
    no_tok = _account({"instagram_auto_reply": {"enabled": True}}, access_token_enc="")
    ok = _account({"instagram_auto_reply": {"enabled": True}, "instagram_messenger_seen": {}})
    bad = _account({"instagram_auto_reply": {"enabled": True}})
    monkeypatch.setattr(IM, "_worker_db", lambda: _DB([off, no_tok, ok, bad]))
    monkeypatch.setattr(IM, "_process_account", AsyncMock(side_effect=[2, RuntimeError("x")]))
    import sqlalchemy.orm.attributes as SOA

    monkeypatch.setattr(SOA, "flag_modified", lambda *a, **k: None)

    out = await IM._poll_instagram_messenger_async()
    assert out == {"accounts_checked": 2, "replies_sent": 2, "errors": 1, "skipped_no_token": 1}
    assert "instagram_messenger_last_checked" in ok.meta_data


# ── human handoff notification ───────────────────────────────────────


@pytest.mark.asyncio
async def test_notify_human_handoff(monkeypatch):
    acct = _account({})
    monkeypatch.setattr(IM, "get_settings", _settings)

    # no slack transport configured → early return, no email either
    await IM._notify_human_handoff(acct, "Ada", "t1", "angry msg")

    # webhook configured → slack post; email skipped (no DIGEST_EMAIL_TO)
    import app.services.slack_notifications as SN

    post = AsyncMock()
    monkeypatch.setattr(SN, "_post_slack_text", post)
    s = _settings()
    s.SLACK_WEBHOOK_URL = "http://hook"
    monkeypatch.setattr(IM, "get_settings", lambda: s)
    await IM._notify_human_handoff(acct, "Ada", "t1", "angry msg")
    post.assert_awaited_once()
    assert "Ada" in post.await_args.kwargs["text"]

    # slack raises → swallowed; email fires when DIGEST_EMAIL_TO set
    post.side_effect = RuntimeError("slack down")
    import app.services.email_digest as ED

    send = AsyncMock()
    monkeypatch.setattr(ED, "send_email", send)
    s.DIGEST_EMAIL_TO = "ops@x.io"
    await IM._notify_human_handoff(acct, "Ada", "t1", "angry msg")
    send.assert_awaited_once()

    # email raises → swallowed
    send.side_effect = RuntimeError("smtp down")
    await IM._notify_human_handoff(acct, "Ada", "t1", "angry msg")


# ── _process_account: client selection ───────────────────────────────


@pytest.mark.asyncio
async def test_process_rate_limited_uses_bridge(monkeypatch):
    _wire(monkeypatch, rate_limited=True)
    factory = Mock()
    monkeypatch.setattr(IM, "InstagramAPIClient", factory)
    bridge = _bridge(
        convos={"conversations": [{"id": "c1", "participant_id": "u1", "name": "Ada"}]}, msgs={"messages": [{"is_sent_by_viewer": False, "text": "hi"}]}
    )
    _wire_bridge(monkeypatch, bridge)
    acct = _account({"instagram_auto_reply": {"enabled": True}})

    n = await IM._process_account(None, acct, {}, {}, "t", "a", "u")
    assert n == 1
    factory.assert_not_called()  # Graph API skipped while breaker open
    bridge.send_instagram_dm_message.assert_awaited_once_with("u1", "reply")


@pytest.mark.asyncio
async def test_process_graph_probe_failures(monkeypatch):
    _wire(monkeypatch)
    acct = _account({})

    # probe raises app-level rate limit → breaker set, bridge fallback
    monkeypatch.setattr(
        IM, "InstagramAPIClient", lambda **k: SimpleNamespace(get_conversations=AsyncMock(side_effect=_err(429, "request limit reached")), get_me=AsyncMock())
    )
    bridge = _bridge()
    _wire_bridge(monkeypatch, bridge)
    n = await IM._process_account(None, acct, {}, {}, "t", "a", "u")
    assert n == 0  # no conversations on the bridge
    IM.set_instagram_app_rate_limited.assert_awaited_once()

    # generic probe failure → bridge; bridge session fails → 0
    monkeypatch.setattr(
        IM, "InstagramAPIClient", lambda **k: SimpleNamespace(get_conversations=AsyncMock(side_effect=RuntimeError("probe")), get_me=AsyncMock())
    )
    bridge.ensure_session = AsyncMock(side_effect=RuntimeError("no session"))
    n = await IM._process_account(None, acct, {}, {}, "t", "a", "u")
    assert n == 0


@pytest.mark.asyncio
async def test_process_convos_fetch_errors(monkeypatch):
    _wire(monkeypatch)
    acct = _account({})

    # probe succeeds, fetch raises app-level rate limit → breaker + 0
    client = _graph_client()
    client.get_conversations.side_effect = [{"data": []}, _err(400, '{"code": 4}')]
    monkeypatch.setattr(IM, "InstagramAPIClient", lambda **k: client)
    n = await IM._process_account(None, acct, {}, {}, "t", "a", "u")
    assert n == 0
    IM.set_instagram_app_rate_limited.assert_awaited_once()

    # non-limit API error on fetch → 0
    client.get_conversations.side_effect = [{"data": []}, _err(500, "server")]
    n = await IM._process_account(None, acct, {}, {}, "t", "a", "u")
    assert n == 0

    # generic fetch error → 0
    client.get_conversations.side_effect = [{"data": []}, RuntimeError("net")]
    n = await IM._process_account(None, acct, {}, {}, "t", "a", "u")
    assert n == 0


@pytest.mark.asyncio
async def test_process_get_me_failure(monkeypatch):
    _wire(monkeypatch)
    acct = _account({})
    client = _graph_client(convos={"data": []})
    client.get_me.side_effect = RuntimeError("me boom")
    monkeypatch.setattr(IM, "InstagramAPIClient", lambda **k: client)
    n = await IM._process_account(None, acct, {}, {}, "t", "a", "u")
    assert n == 0  # get_me failure is non-fatal; empty convos → 0


@pytest.mark.asyncio
async def test_process_bridge_fetch_error(monkeypatch):
    _wire(monkeypatch, rate_limited=True)
    bridge = _bridge(convos={"error": "not logged in"})
    _wire_bridge(monkeypatch, bridge)
    acct = _account({})
    n = await IM._process_account(None, acct, {}, {}, "t", "a", "u")
    assert n == 0


# ── _process_account: conversation guards ────────────────────────────


@pytest.mark.asyncio
async def test_process_convo_guards(monkeypatch):
    _wire(monkeypatch)
    acct = _account({})
    client = _graph_client(
        convos={
            "data": [
                _convo(cid=""),  # no convo id
                _convo(cid="c2", participant=""),  # no recipient id
                _convo(cid="c3", embedded=[]),  # no embedded message → skip
            ]
        }
    )
    # participant "" also fails recipient check via my_ids? "u1" used above
    client.get_conversations.return_value = {
        "data": [
            _convo(cid=""),
            {"id": "c2", "participants": {"data": [{"id": "acc-ig-1"}]}},
            {"id": "cx", "participants": {"data": [{"name": "NoID"}]}},
            _convo(cid="c3", embedded=[]),
            _convo(cid="c4", embedded=[{"from": {"id": "me-igsid"}, "message": "out"}]),
            _convo(cid="c5", embedded=[{"from": {"id": "u1"}, "message": "old"}]),
        ]
    }
    monkeypatch.setattr(IM, "InstagramAPIClient", lambda **k: client)
    seen = {"c5": "old"}
    n = await IM._process_account(None, acct, {}, seen, "t", "a", "u")
    assert n == 0
    client.get_dm_messages.assert_not_awaited()  # all skipped on guards


@pytest.mark.asyncio
async def test_process_bridge_msg_error(monkeypatch):
    _wire(monkeypatch, rate_limited=True)
    bridge = _bridge(convos={"conversations": [{"id": "c1", "participant_id": "u1", "name": "Ada"}]}, msgs={"error": "fetch failed"})
    _wire_bridge(monkeypatch, bridge)
    n = await IM._process_account(None, _account({}), {}, {}, "t", "a", "u")
    assert n == 0

    # empty message list → skip
    bridge.get_instagram_dm_messages = AsyncMock(return_value={"messages": []})
    n = await IM._process_account(None, _account({}), {}, {}, "t", "a", "u")
    assert n == 0

    # only outbound messages → no inbound → skip
    bridge.get_instagram_dm_messages = AsyncMock(return_value={"messages": [{"is_sent_by_viewer": True, "text": "mine"}]})
    n = await IM._process_account(None, _account({}), {}, {}, "t", "a", "u")
    assert n == 0


# ── _process_account: reply paths ────────────────────────────────────


@pytest.mark.asyncio
async def test_process_graph_happy(monkeypatch):
    _wire(monkeypatch)
    acct = _account({})
    client = _graph_client(
        convos={"data": [_convo(embedded=[{"from": {"id": "u1"}, "message": "hi"}])]},
        msgs={
            "data": [
                {"from": {"id": "me-igsid"}, "message": "earlier reply"},
                {"from": {"id": "u1"}, "message": "hi"},
            ]
        },
    )
    monkeypatch.setattr(IM, "InstagramAPIClient", lambda **k: client)

    seen: dict = {}
    n = await IM._process_account(None, acct, {}, seen, "t", "a", "u")
    assert n == 1
    client.send_typing_indicator.assert_awaited_once_with("u1")
    client.send_dm.assert_awaited_once_with("u1", "reply")
    client.mark_dm_read.assert_awaited_once()
    assert seen == {"c1": "hi"}
    assert IM.store_message_memory.await_count == 2
    IM.set_cooldown.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_multi_inbound_combines(monkeypatch):
    _wire(monkeypatch)
    acct = _account({})
    client = _graph_client(
        convos={"data": [_convo(embedded=[{"from": {"id": "u1"}, "message": "second"}])]},
        msgs={
            "data": [
                {"from": {"id": "u1"}, "message": "first"},
                {"from": {"id": "u1"}, "message": "second"},
            ]
        },
    )
    monkeypatch.setattr(IM, "InstagramAPIClient", lambda **k: client)
    seen: dict = {}
    n = await IM._process_account(None, acct, {}, seen, "t", "a", "u")
    assert n == 1
    assert seen["c1"] == "second\nfirst"  # reversed order → chronological


@pytest.mark.asyncio
async def test_process_state_guards(monkeypatch):
    _wire(monkeypatch)
    acct = _account({})
    client = _graph_client(
        convos={"data": [_convo(embedded=[{"from": {"id": "u1"}, "message": "hi"}])]}, msgs={"data": [{"from": {"id": "u1"}, "message": "hi"}]}
    )
    monkeypatch.setattr(IM, "InstagramAPIClient", lambda **k: client)

    # combined text already seen → skip
    assert await IM._process_account(None, acct, {}, {"c1": "hi"}, "t", "a", "u") == 0

    # multi-message combined text already seen → skip at line 446
    client.get_dm_messages.side_effect = None
    client.get_dm_messages.return_value = {
        "data": [
            {"from": {"id": "u1"}, "message": "first"},
            {"from": {"id": "u1"}, "message": "second"},
        ]
    }
    assert await IM._process_account(None, acct, {}, {"c1": "second\nfirst"}, "t", "a", "u") == 0
    client.get_dm_messages.return_value = {"data": [{"from": {"id": "u1"}, "message": "hi"}]}

    # cooldown active → skip
    IM.check_cooldown = AsyncMock(return_value=False)
    assert await IM._process_account(None, acct, {}, {}, "t", "a", "u") == 0
    IM.check_cooldown = AsyncMock(return_value=True)

    # paused thread → skip
    IM.is_thread_paused = AsyncMock(return_value=True)
    assert await IM._process_account(None, acct, {}, {}, "t", "a", "u") == 0


@pytest.mark.asyncio
async def test_process_frustrated_handoff(monkeypatch):
    _wire(monkeypatch)
    acct = _account({})
    client = _graph_client(
        convos={"data": [_convo(embedded=[{"from": {"id": "u1"}, "message": "terrible service"}])]},
        msgs={"data": [{"from": {"id": "u1"}, "message": "terrible service"}]},
    )
    monkeypatch.setattr(IM, "InstagramAPIClient", lambda **k: client)
    IM.is_frustrated_message = lambda t: True

    n = await IM._process_account(None, acct, {}, {}, "t", "a", "u")
    assert n == 0
    IM.pause_thread.assert_awaited_once()
    IM._notify_human_handoff.assert_awaited_once()
    client.send_dm.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_lead_capture(monkeypatch):
    _wire(monkeypatch)
    acct = _account({})
    client = _graph_client(
        convos={"data": [_convo(embedded=[{"from": {"id": "u1"}, "message": "pricing?"}])]}, msgs={"data": [{"from": {"id": "u1"}, "message": "pricing?"}]}
    )
    monkeypatch.setattr(IM, "InstagramAPIClient", lambda **k: client)

    import app.services.lead_capture as LC

    lead = AsyncMock(return_value=SimpleNamespace(text="lead reply"))
    monkeypatch.setattr(LC, "handle_lead_capture_message", lead)

    seen: dict = {}
    n = await IM._process_account(None, acct, {}, seen, "t", "a", "u")
    assert n == 1
    client.send_dm.assert_awaited_once_with("u1", "lead reply")
    client.mark_dm_read.assert_awaited_once()
    assert seen["c1"] == "pricing?"
    IM.generate_contextual_reply.assert_not_awaited()  # short-circuited

    # lead handler raising → falls through to normal reply flow
    lead.side_effect = RuntimeError("lead boom")
    client.send_dm.reset_mock()
    n = await IM._process_account(None, acct, {}, {}, "t", "a", "u")
    assert n == 1
    client.send_dm.assert_awaited_once_with("u1", "reply")

    # mark_dm_read failure in the lead path is non-fatal
    lead.side_effect = None
    client.send_dm.reset_mock()
    client.mark_dm_read.side_effect = RuntimeError("read boom")
    n = await IM._process_account(None, acct, {}, {}, "t", "a", "u")
    assert n == 1
    client.send_dm.assert_awaited_once_with("u1", "lead reply")


@pytest.mark.asyncio
async def test_process_lead_capture_bridge(monkeypatch):
    _wire(monkeypatch, rate_limited=True)
    acct = _account({})
    bridge = _bridge(
        convos={"conversations": [{"id": "c1", "participant_id": "u1", "name": "Ada"}]}, msgs={"messages": [{"is_sent_by_viewer": False, "text": "hi"}]}
    )
    _wire_bridge(monkeypatch, bridge)

    import app.services.lead_capture as LC

    monkeypatch.setattr(LC, "handle_lead_capture_message", AsyncMock(return_value=SimpleNamespace(text="lr")))
    n = await IM._process_account(None, acct, {}, {}, "t", "a", "u")
    assert n == 1
    bridge.send_instagram_dm_message.assert_awaited_once_with("u1", "lr")


@pytest.mark.asyncio
async def test_process_echo_guard_and_fallback(monkeypatch):
    _wire(monkeypatch)
    acct = _account({})
    client = _graph_client(
        convos={"data": [_convo(embedded=[{"from": {"id": "u1"}, "message": "hello"}])]}, msgs={"data": [{"from": {"id": "u1"}, "message": "hello"}]}
    )
    monkeypatch.setattr(IM, "InstagramAPIClient", lambda **k: client)

    # AI echoes the user → fallback text
    IM.generate_contextual_reply = AsyncMock(return_value="Hello")
    n = await IM._process_account(None, acct, {"fallback_text": "FB"}, {}, "t", "a", "u")
    assert n == 1
    client.send_dm.assert_awaited_once_with("u1", "FB")

    # AI empty → fallback
    client.send_dm.reset_mock()
    IM.generate_contextual_reply = AsyncMock(return_value="")
    n = await IM._process_account(None, acct, {"fallback_text": "FB2"}, {}, "t", "a", "u")
    client.send_dm.assert_awaited_once_with("u1", "FB2")


@pytest.mark.asyncio
async def test_process_send_failures(monkeypatch):
    _wire(monkeypatch)
    acct = _account({})
    client = _graph_client(
        convos={"data": [_convo(embedded=[{"from": {"id": "u1"}, "message": "hi"}])]}, msgs={"data": [{"from": {"id": "u1"}, "message": "hi"}]}
    )
    monkeypatch.setattr(IM, "InstagramAPIClient", lambda **k: client)

    # send_dm raises → not marked seen, no reply counted
    client.send_dm.side_effect = RuntimeError("send boom")
    seen: dict = {}
    n = await IM._process_account(None, acct, {}, seen, "t", "a", "u")
    assert n == 0 and seen == {}

    # typing indicator + mark_read failures are non-fatal
    client.send_dm.side_effect = None
    client.send_typing_indicator.side_effect = RuntimeError("typing")
    client.mark_dm_read.side_effect = RuntimeError("read")
    n = await IM._process_account(None, acct, {}, seen, "t", "a", "u")
    assert n == 1 and seen["c1"] == "hi"


@pytest.mark.asyncio
async def test_process_bridge_send_error_and_success(monkeypatch):
    _wire(monkeypatch, rate_limited=True)
    acct = _account({})

    bridge = _bridge(
        convos={"conversations": [{"id": "c1", "participant_id": "u1", "name": "Ada"}]},
        msgs={"messages": [{"is_sent_by_viewer": False, "text": "hi"}]},
        send_result={"error": "send failed"},
    )
    _wire_bridge(monkeypatch, bridge)
    seen: dict = {}
    n = await IM._process_account(None, acct, {}, seen, "t", "a", "u")
    assert n == 0 and seen == {}

    bridge.send_instagram_dm_message = AsyncMock(return_value={"ok": True})
    n = await IM._process_account(None, acct, {}, seen, "t", "a", "u")
    assert n == 1 and seen["c1"] == "hi"


@pytest.mark.asyncio
async def test_process_api_error_in_loop(monkeypatch):
    _wire(monkeypatch)
    acct = _account({})
    client = _graph_client(
        convos={
            "data": [
                _convo(cid="c1", embedded=[{"from": {"id": "u1"}, "message": "a"}]),
                _convo(cid="c2", participant="u2", embedded=[{"from": {"id": "u2"}, "message": "b"}]),
            ]
        }
    )
    # rate-limit during message fetch → breaker set + break (c2 skipped)
    client.get_dm_messages.side_effect = _err(400, '{"code": 4}')
    monkeypatch.setattr(IM, "InstagramAPIClient", lambda **k: client)
    n = await IM._process_account(None, acct, {}, {}, "t", "a", "u")
    assert n == 0
    IM.set_instagram_app_rate_limited.assert_awaited_once()
    assert client.get_dm_messages.await_count == 1  # broke out of loop

    # non-limit API error → continue to next convo
    client.get_dm_messages.side_effect = [_err(500, "server"), {"data": [{"from": {"id": "u2"}, "message": "b"}]}]
    n = await IM._process_account(None, acct, {}, {}, "t", "a", "u")
    assert n == 1  # c2 still processed


@pytest.mark.asyncio
async def test_process_generic_error_in_loop(monkeypatch):
    _wire(monkeypatch)
    acct = _account({})
    client = _graph_client(convos={"data": [_convo(embedded=[{"from": {"id": "u1"}, "message": "hi"}])]})
    client.get_dm_messages.side_effect = RuntimeError("fetch boom")
    monkeypatch.setattr(IM, "InstagramAPIClient", lambda **k: client)
    n = await IM._process_account(None, acct, {}, {}, "t", "a", "u")
    assert n == 0  # error logged, loop continues, returns count
