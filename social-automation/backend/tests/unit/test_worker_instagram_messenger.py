"""Unit tests for app/worker/tasks/instagram_messenger.py — IG DM bot poller.

Covers the Meta rate-limit detector, human-handoff Slack/email notify,
the account loop (disabled/no-token/error paths), and _process_account
Graph → browser-bridge fallback chain with the full conversation pipeline
(seen-dedup, cooldown, pause, frustration handoff, lead capture, echo
guard, send/mark-read/memory bookkeeping).
"""

from __future__ import annotations

import json
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


# ── InstagramPrivateDMClient (instagrapi sessionid lane) ─────────────


class _FakeIGClient:
    """Stand-in for instagrapi.Client recording calls."""

    instances: list = []

    def __init__(self) -> None:
        self.calls: list = []
        self.user_id = 42
        self.threads: list = []
        self.msgs: dict = {}
        _FakeIGClient.instances.append(self)

    def set_proxy(self, proxy: str) -> None:
        self.calls.append(("set_proxy", proxy))

    def set_locale(self, locale: str) -> None:
        self.calls.append(("set_locale", locale))

    def set_timezone_offset(self, offset: int) -> None:
        self.calls.append(("set_timezone_offset", offset))

    def login_by_sessionid(self, sid: str) -> None:
        self.calls.append(("login_by_sessionid", sid))

    def load_settings(self, path: str) -> None:
        self.calls.append(("load_settings", path))
        self._loaded = open(path).read()

    def dump_settings(self, path: str) -> None:
        self.calls.append(("dump_settings", path))

    def direct_threads(self, amount: int = 25):
        return self.threads[:amount]

    def direct_messages(self, thread_id: int, amount: int = 20):
        return self.msgs.get(thread_id, [])[:amount]

    def direct_send(self, text: str, user_ids: list) -> None:
        self.calls.append(("direct_send", text, user_ids))

    def direct_message_seen(self, thread_id: int, msg_id) -> None:
        self.calls.append(("direct_message_seen", thread_id, msg_id))


def _fake_instagrapi(monkeypatch, client: _FakeIGClient | None = None):
    import sys
    import types

    _FakeIGClient.instances = []
    mod = types.ModuleType("instagrapi")
    mod.Client = _FakeIGClient
    monkeypatch.setitem(sys.modules, "instagrapi", mod)
    return client


def _ig_thread(tid="t1", users=None, messages=None):
    return SimpleNamespace(
        id=tid,
        users=users
        if users is not None
        else [
            SimpleNamespace(pk=42, username="me"),
            SimpleNamespace(pk=7, username="bob"),
        ],
        messages=messages if messages is not None else [],
    )


@pytest.mark.asyncio
async def test_private_dm_client_lazy_init_and_probe(monkeypatch):
    _fake_instagrapi(monkeypatch)
    c = IG.InstagramPrivateDMClient(session_id="sid-1", proxy="http://p:1")
    await c.probe()
    cl = _FakeIGClient.instances[0]
    assert ("set_proxy", "http://p:1") in cl.calls
    assert ("set_locale", "el_GR") in cl.calls
    assert ("set_timezone_offset", 10800) in cl.calls
    assert ("login_by_sessionid", "sid-1") in cl.calls
    # second call reuses the same client — no re-login
    await c.probe()
    assert len(_FakeIGClient.instances) == 1


@pytest.mark.asyncio
async def test_private_dm_client_no_proxy_and_proxy_failure(monkeypatch):
    _fake_instagrapi(monkeypatch)
    c = IG.InstagramPrivateDMClient(session_id="sid-2")
    await c.probe()
    cl = _FakeIGClient.instances[0]
    assert not any(call[0] == "set_proxy" for call in cl.calls)

    class _BadProxy(_FakeIGClient):
        def set_proxy(self, proxy):
            raise RuntimeError("no proxy")

    import sys
    import types

    mod = types.ModuleType("instagrapi")
    mod.Client = _BadProxy
    monkeypatch.setitem(sys.modules, "instagrapi", mod)
    c2 = IG.InstagramPrivateDMClient(session_id="sid-3", proxy="http://bad")
    await c2.probe()  # proxy failure is logged, login still proceeds
    assert _BadProxy.instances[-1].calls[-1] == ("login_by_sessionid", "sid-3")


@pytest.mark.asyncio
async def test_private_dm_client_conversations_and_messages(monkeypatch):
    _fake_instagrapi(monkeypatch)
    c = IG.InstagramPrivateDMClient(session_id="sid-4")
    await c.probe()
    cl = _FakeIGClient.instances[0]
    cl.threads = [
        _ig_thread(
            tid="t1",
            messages=[SimpleNamespace(user_id=7, text="hello there")],
        ),
        _ig_thread(tid="t2", users=[SimpleNamespace(pk=42, username="me")], messages=[]),
    ]
    cl.msgs[1] = [
        SimpleNamespace(user_id=7, text="first", id="m1"),
        SimpleNamespace(user_id=42, text="reply", id="m2"),
    ]

    convos = await c.get_conversations(limit=10)
    assert len(convos["data"]) == 2
    t1 = convos["data"][0]
    assert t1["id"] == "t1"
    # self (pk 42) filtered out of participants; embedded latest inbound msg
    assert t1["participants"]["data"] == [{"id": "7", "username": "bob"}]
    assert t1["messages"]["data"] == [{"from": {"id": "7"}, "message": "hello there"}]
    # thread with no messages → empty embedded list
    assert convos["data"][1]["messages"]["data"] == []

    msgs = await c.get_dm_messages("1", limit=5)
    assert msgs["data"] == [
        {"from": {"id": "7"}, "message": "first"},
        {"from": {"id": "42"}, "message": "reply"},
    ]

    me = await c.get_me()
    assert me == {"user_id": "42"}


@pytest.mark.asyncio
async def test_private_dm_client_send_and_mark_read(monkeypatch):
    _fake_instagrapi(monkeypatch)
    c = IG.InstagramPrivateDMClient(session_id="sid-5")
    await c.probe()
    cl = _FakeIGClient.instances[0]
    cl.msgs[9] = [SimpleNamespace(user_id=7, text="hi", id="m9")]

    out = await c.send_dm("7", "hey")
    assert out == {"id": "ok"}
    assert ("direct_send", "hey", [7]) in cl.calls

    await c.mark_dm_read("9")
    assert ("direct_message_seen", 9, "m9") in cl.calls

    # empty thread → mark_dm_read is a no-op beyond the fetch
    cl.msgs[10] = []
    await c.mark_dm_read("10")
    assert not any(call[0] == "direct_message_seen" and call[1] == 10 for call in cl.calls)

    assert await c.send_typing_indicator("7") == {}


# ── _process_account private-API fallback ─────────────────────────────


@pytest.mark.asyncio
async def test_process_account_private_api_fallback(monkeypatch):
    _bot_patches(monkeypatch)
    # Graph probe fails with a non-rate-limit error → private lane tried
    bad_graph = _graph_client()
    bad_graph.get_conversations.side_effect = RuntimeError("token dead")
    monkeypatch.setattr(IG, "InstagramAPIClient", lambda **kw: bad_graph)
    monkeypatch.setattr(IG, "decrypt_field", lambda v: "sid-enc" if v else None)

    private = _graph_client(
        conversations=[_convo(last_msg="hi there")],
        messages=[{"from": {"id": "user-9"}, "message": "hi there"}],
    )
    private.probe = AsyncMock()
    monkeypatch.setattr(IG, "InstagramPrivateDMClient", lambda **kw: private)

    account = _account(meta_data={"instagram_auto_reply": {"enabled": True}, "private_api_session_id": "enc-sid"})
    n = await IG._process_account(SimpleNamespace(), account, {"enabled": True}, {}, "", "", "http://dmr")
    assert n == 1
    private.probe.assert_awaited_once()
    private.get_conversations.assert_awaited()
    private.send_dm.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_account_private_api_probe_fails_to_bridge(monkeypatch):
    _bot_patches(monkeypatch)
    bad_graph = _graph_client()
    bad_graph.get_conversations.side_effect = RuntimeError("token dead")
    monkeypatch.setattr(IG, "InstagramAPIClient", lambda **kw: bad_graph)
    monkeypatch.setattr(IG, "decrypt_field", lambda v: "sid-enc" if v else None)

    private = _graph_client()
    private.probe = AsyncMock(side_effect=RuntimeError("bad session"))
    monkeypatch.setattr(IG, "InstagramPrivateDMClient", lambda **kw: private)

    bridge = SimpleNamespace(
        ensure_session=AsyncMock(),
        get_instagram_dm_conversations=AsyncMock(return_value={"conversations": [{"id": "bc1", "participant_id": "u9", "name": "bob"}]}),
        get_instagram_dm_messages=AsyncMock(return_value={"messages": [{"is_sent_by_viewer": False, "text": "yo"}]}),
        send_instagram_dm_message=AsyncMock(return_value={"ok": True}),
    )
    monkeypatch.setattr(IG, "BrowserBridgeClient", lambda **kw: bridge)

    class _BSession:
        async def __aenter__(self):
            return bridge

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(IG, "browser_session", lambda *a, **kw: _BSession())
    account = _account(meta_data={"instagram_auto_reply": {"enabled": True}, "private_api_session_id": "enc-sid"})
    n = await IG._process_account(SimpleNamespace(), account, {"enabled": True}, {}, "", "", "http://dmr")
    assert n == 1
    bridge.send_instagram_dm_message.assert_awaited()


@pytest.mark.asyncio
async def test_process_account_no_private_sid_goes_to_bridge(monkeypatch):
    _bot_patches(monkeypatch)
    bad_graph = _graph_client()
    bad_graph.get_conversations.side_effect = RuntimeError("token dead")
    monkeypatch.setattr(IG, "InstagramAPIClient", lambda **kw: bad_graph)
    monkeypatch.setattr(IG, "decrypt_field", lambda v: None)
    factory = Mock(side_effect=AssertionError("private client must not be built"))
    monkeypatch.setattr(IG, "InstagramPrivateDMClient", factory)

    bridge = SimpleNamespace(ensure_session=AsyncMock(side_effect=RuntimeError("no bridge")))
    monkeypatch.setattr(IG, "BrowserBridgeClient", lambda **kw: bridge)
    n = await IG._process_account(SimpleNamespace(), _account(), {"enabled": True}, {}, "", "", "http://dmr")
    assert n == 0
    factory.assert_not_called()


# ── poll loop: private session qualifies without access token ─────────


@pytest.mark.asyncio
async def test_poll_private_session_counts_as_credential(monkeypatch):
    no_token_with_session = _account(
        access_token_enc=None,
        meta_data={
            "instagram_auto_reply": {"enabled": True},
            "private_api_session_id": "sid",
        },
    )
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_Res([no_token_with_session])),
        commit=AsyncMock(),
    )
    monkeypatch.setattr(IG, "_worker_db", lambda: _SessionCtx(db))
    monkeypatch.setattr(IG, "flag_modified", lambda *a: None, raising=False)
    import sqlalchemy.orm.attributes as SOA

    monkeypatch.setattr(SOA, "flag_modified", lambda *a: None)
    proc = AsyncMock(return_value=1)
    monkeypatch.setattr(IG, "_process_account", proc)

    out = await IG._poll_instagram_messenger_async()
    assert out["accounts_checked"] == 1
    assert out["skipped_no_token"] == 0
    proc.assert_awaited_once()


@pytest.mark.asyncio
async def test_private_dm_client_settings_blob_path(monkeypatch):
    """settings_blob → load_settings(fingerprint) THEN login_by_sessionid."""
    _fake_instagrapi(monkeypatch)
    blob = json.dumps({"device": "fingerprint-1", "cookies": {}})
    c = IG.InstagramPrivateDMClient(session_id="sid-9", settings_blob=blob)
    await c.probe()
    cl = _FakeIGClient.instances[0]
    kinds = [call[0] for call in cl.calls]
    assert "load_settings" in kinds
    assert kinds.index("load_settings") < kinds.index("login_by_sessionid")
    # blob content was actually written to the temp file load_settings read
    assert cl._loaded == blob


@pytest.mark.asyncio
async def test_process_account_private_api_uses_settings_blob(monkeypatch):
    _bot_patches(monkeypatch)
    bad_graph = _graph_client()
    bad_graph.get_conversations.side_effect = RuntimeError("token dead")
    monkeypatch.setattr(IG, "InstagramAPIClient", lambda **kw: bad_graph)
    monkeypatch.setattr(IG, "decrypt_field", lambda v: "sid-enc" if "session_id" in str(v) else ("blob-enc" if "settings" in str(v) else None))

    private = _graph_client(
        conversations=[_convo(last_msg="yo")],
        messages=[{"from": {"id": "user-9"}, "message": "yo"}],
    )
    private.probe = AsyncMock()
    captured = {}

    def _factory(**kw):
        captured.update(kw)
        return private

    monkeypatch.setattr(IG, "InstagramPrivateDMClient", _factory)
    account = _account(
        meta_data={
            "instagram_auto_reply": {"enabled": True},
            "private_api_session_id": "enc-session_id",
            "private_api_session_settings": "enc-settings",
        }
    )
    n = await IG._process_account(SimpleNamespace(), account, {"enabled": True}, {}, "", "", "http://dmr")
    assert n == 1
    assert captured["session_id"] == "sid-enc"
    assert captured["settings_blob"] == "blob-enc"
