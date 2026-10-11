"""Unit tests for twitter_messenger + tiktok_messenger worker tasks."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.worker.tasks.linkedin_messenger as LI
import app.worker.tasks.personal_messenger as PM
import app.worker.tasks.threads_messenger as TH
import app.worker.tasks.tiktok_messenger as TT
import app.worker.tasks.twitter_messenger as TW


def _account(platform_meta: dict):
    return SimpleNamespace(
        id=uuid.uuid4(), team_id=uuid.uuid4(),
        display_name="Cloudless", username="cl",
        meta_data=platform_meta)


def _wire(mod, monkeypatch, bridge=None, *, session_status="active"):
    """Patch all external seams of a messenger worker module."""
    if bridge is None:
        bridge = SimpleNamespace(
            ensure_session=AsyncMock(return_value={
                "status": session_status}),
        )
    monkeypatch.setattr(mod, "BrowserBridgeClient",
                        lambda *a, **k: bridge)

    class _BS:
        async def __aenter__(self):
            return bridge

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(mod, "browser_session", lambda *a, **k: _BS())
    monkeypatch.setattr(mod, "check_cooldown", AsyncMock(return_value=True))
    monkeypatch.setattr(mod, "is_thread_paused",
                        AsyncMock(return_value=False))
    monkeypatch.setattr(mod, "detect_intent", AsyncMock(return_value="i"))
    monkeypatch.setattr(mod, "retrieve_brand_context",
                        AsyncMock(return_value="ctx"))
    monkeypatch.setattr(mod, "generate_contextual_reply",
                        AsyncMock(return_value="reply"))
    monkeypatch.setattr(mod, "store_message_memory", AsyncMock())
    monkeypatch.setattr(mod, "set_cooldown", AsyncMock())
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    return bridge


# ── twitter poller ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_twitter_poller(monkeypatch):
    class _DB:
        def __init__(self, accounts):
            self.accounts = accounts
            self.commit = AsyncMock()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, *a):
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: self.accounts))

    # no accounts → zeroed stats
    monkeypatch.setattr(TW, "_worker_db", lambda: _DB([]))
    out = await TW._poll_twitter_messenger_async()
    assert out == {"accounts_checked": 0, "replies_sent": 0,
                   "errors": 0, "skipped_no_session": 0}

    # disabled account skipped; enabled processed; error counted
    acct_on = _account({"twitter_auto_reply": {"enabled": True},
                        "twitter_messenger_seen": {}})
    acct_off = _account({"twitter_auto_reply": {"enabled": False}})
    acct_err = _account({"twitter_auto_reply": {"enabled": True}})
    monkeypatch.setattr(TW, "_worker_db",
                        lambda: _DB([acct_off, acct_on, acct_err]))
    proc = AsyncMock(side_effect=[2, RuntimeError("x")])
    monkeypatch.setattr(TW, "_process_account", proc)
    import sqlalchemy.orm.attributes as SOA
    monkeypatch.setattr(SOA, "flag_modified", lambda *a, **k: None)

    out = await TW._poll_twitter_messenger_async()
    assert out["accounts_checked"] == 2
    assert out["replies_sent"] == 2 and out["errors"] == 1
    assert "twitter_messenger_last_checked" in acct_on.meta_data


# ── twitter _process_account ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_twitter_process_guards(monkeypatch):
    acct = _account({})
    cfg = {"cooldown_seconds": 300}
    seen: dict = {}

    # session not active → 0
    b = _wire(TW, monkeypatch, session_status="inactive")
    assert await TW._process_account(acct, cfg, seen, "t", "a", "u") == 0

    # session check raises → 0
    b = _wire(TW, monkeypatch)
    b.ensure_session = AsyncMock(side_effect=RuntimeError("x"))
    assert await TW._process_account(acct, cfg, seen, "t", "a", "u") == 0

    # convos fetch raises → 0
    b = _wire(TW, monkeypatch)
    b.get_twitter_dm_conversations = AsyncMock(
        side_effect=RuntimeError("x"))
    assert await TW._process_account(acct, cfg, seen, "t", "a", "u") == 0

    # no conversations → 0
    b = _wire(TW, monkeypatch)
    b.get_twitter_dm_conversations = AsyncMock(
        return_value={"conversations": []})
    assert await TW._process_account(acct, cfg, seen, "t", "a", "u") == 0


@pytest.mark.asyncio
async def test_twitter_process_convo_guards(monkeypatch):
    acct = _account({})
    cfg = {"cooldown_seconds": 300}

    def _bridge(msgs, convos=None):
        b = _wire(TW, monkeypatch)
        b.get_twitter_dm_conversations = AsyncMock(return_value={
            "conversations": convos or [{"thread_id": "c1",
                                          "name": "User"}]})
        b.get_twitter_dm_messages = AsyncMock(
            return_value={"messages": msgs})
        b.send_twitter_dm_message = AsyncMock()
        return b

    seen: dict = {}
    # no convo_id → skipped
    _bridge([{"text": "hi"}], convos=[{"thread_id": ""}])
    assert await TW._process_account(acct, cfg, seen, "", "", "") == 0
    # empty messages → skip
    _bridge([])
    assert await TW._process_account(acct, cfg, seen, "", "", "") == 0
    # no text message → skip
    _bridge([{"text": "  "}])
    assert await TW._process_account(acct, cfg, seen, "", "", "") == 0
    # already seen → skip
    _bridge([{"text": "hi"}])
    assert await TW._process_account(acct, cfg, {"c1": "hi"}, "", "", "") == 0
    # cooldown active → skip
    _bridge([{"text": "hi"}])
    TW.check_cooldown = AsyncMock(return_value=False)
    assert await TW._process_account(acct, cfg, seen, "", "", "") == 0
    TW.check_cooldown = AsyncMock(return_value=True)
    # paused thread → skip
    _bridge([{"text": "hi"}])
    TW.is_thread_paused = AsyncMock(return_value=True)
    assert await TW._process_account(acct, cfg, seen, "", "", "") == 0


@pytest.mark.asyncio
async def test_twitter_process_happy(monkeypatch):
    acct = _account({})
    b = _wire(TW, monkeypatch)
    b.get_twitter_dm_conversations = AsyncMock(return_value={
        "conversations": [{"thread_id": "c1", "name": "Ada"},
                          {"thread_id": "c2", "name": "Bad"}]})
    b.get_twitter_dm_messages = AsyncMock(side_effect=[
        {"messages": [{"text": "price?", "id": "m1"}]},
        RuntimeError("msg fetch boom")])
    b.send_twitter_dm_message = AsyncMock()

    seen: dict = {}
    n = await TW._process_account(
        acct, {"cooldown_seconds": 1, "fallback_text": "fb"},
        seen, "t", "a", "u")
    assert n == 1  # c2's error swallowed, loop continued
    b.send_twitter_dm_message.assert_awaited_once_with("c1", "reply")
    assert seen == {"c1": "price?"}
    # both memory writes
    assert TW.store_message_memory.await_count == 2
    TW.set_cooldown.assert_awaited_once()

    # empty reply → fallback_text used
    b2 = _wire(TW, monkeypatch)
    TW.generate_contextual_reply = AsyncMock(return_value="")
    b2.get_twitter_dm_conversations = AsyncMock(return_value={
        "conversations": [{"thread_id": "c1", "name": "Ada"}]})
    b2.get_twitter_dm_messages = AsyncMock(return_value={
        "messages": [{"text": "yo"}]})
    b2.send_twitter_dm_message = AsyncMock()
    n = await TW._process_account(
        acct, {"fallback_text": "FBTXT"}, {}, "t", "a", "u")
    b2.send_twitter_dm_message.assert_awaited_once_with("c1", "FBTXT")


# ── tiktok _process_account (clone + sender-me guard) ─────────────────


@pytest.mark.asyncio
async def test_tiktok_process(monkeypatch):
    acct = _account({})
    cfg = {"cooldown_seconds": 300}

    def _bridge(msgs):
        b = _wire(TT, monkeypatch)
        b.get_tiktok_dm_conversations = AsyncMock(return_value={
            "conversations": [{"thread_id": "c1", "name": "User"}]})
        b.get_tiktok_dm_messages = AsyncMock(
            return_value={"messages": msgs})
        b.send_tiktok_dm_message = AsyncMock()
        return b

    seen: dict = {}
    # last message from "me" → skipped (no new inbound)
    _bridge([{"text": "q?", "sender": "them"},
                 {"text": "answer", "sender": "me"}])
    assert await TT._process_account(acct, cfg, seen, "", "", "") == 0

    # last message inbound → replied
    b = _bridge([{"text": "answer", "sender": "me"},
                 {"text": "follow-up?", "sender": "them"}])
    assert await TT._process_account(acct, cfg, seen, "", "", "") == 1
    b.send_tiktok_dm_message.assert_awaited_once_with("c1", "reply")
    assert seen["c1"] == "follow-up?"

    # no sender field → treated as inbound
    _bridge([{"text": "no sender"}])
    assert await TT._process_account(acct, cfg, {}, "", "", "") == 1


@pytest.mark.asyncio
async def test_tiktok_poller(monkeypatch):
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, *a):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(
                all=lambda: [acct]))

        commit = AsyncMock()

    acct = _account({"tiktok_auto_reply": {"enabled": True},
                     "tiktok_messenger_seen": {}})
    monkeypatch.setattr(TT, "_worker_db", lambda: _DB())
    monkeypatch.setattr(TT, "_process_account", AsyncMock(return_value=1))
    import sqlalchemy.orm.attributes as SOA
    monkeypatch.setattr(SOA, "flag_modified", lambda *a, **k: None)
    out = await TT._poll_tiktok_messenger_async()
    assert out["replies_sent"] == 1 and out["accounts_checked"] == 1


# ── _run_async wrapper ────────────────────────────────────────────────


def test_run_async():
    async def _coro():
        return 42

    # sync celery path (no running loop)
    out = TW._run_async(_coro())
    assert out == 42
    out = TT._run_async(_coro())
    assert out == 42


@pytest.mark.asyncio
async def test_tiktok_process_guards(monkeypatch):
    acct = _account({})
    cfg = {"cooldown_seconds": 300}
    seen: dict = {}

    # session not active → 0
    _wire(TT, monkeypatch, session_status="inactive")
    assert await TT._process_account(acct, cfg, seen, "", "", "") == 0

    # session raises → 0
    b = _wire(TT, monkeypatch)
    b.ensure_session = AsyncMock(side_effect=RuntimeError("x"))
    assert await TT._process_account(acct, cfg, seen, "", "", "") == 0

    # convos fetch raises → 0
    b = _wire(TT, monkeypatch)
    b.get_tiktok_dm_conversations = AsyncMock(
        side_effect=RuntimeError("x"))
    assert await TT._process_account(acct, cfg, seen, "", "", "") == 0

    # no conversations → 0
    b = _wire(TT, monkeypatch)
    b.get_tiktok_dm_conversations = AsyncMock(
        return_value={"conversations": []})
    assert await TT._process_account(acct, cfg, seen, "", "", "") == 0


@pytest.mark.asyncio
async def test_tiktok_convo_guards(monkeypatch):
    acct = _account({})
    cfg = {"cooldown_seconds": 300}

    def _bridge(msgs, convos=None):
        b = _wire(TT, monkeypatch)
        b.get_tiktok_dm_conversations = AsyncMock(return_value={
            "conversations": convos or [{"thread_id": "c1"}]})
        b.get_tiktok_dm_messages = AsyncMock(
            return_value={"messages": msgs})
        b.send_tiktok_dm_message = AsyncMock()
        return b

    seen: dict = {}
    # no convo_id → skip
    _bridge([{"text": "hi"}], convos=[{"thread_id": ""}])
    assert await TT._process_account(acct, cfg, seen, "", "", "") == 0
    # empty messages → skip
    _bridge([])
    assert await TT._process_account(acct, cfg, seen, "", "", "") == 0
    # blank text → skip
    _bridge([{"text": " "}])
    assert await TT._process_account(acct, cfg, seen, "", "", "") == 0
    # already seen → skip
    _bridge([{"text": "hi"}])
    assert await TT._process_account(acct, cfg, {"c1": "hi"}, "", "", "") == 0
    # cooldown → skip
    _bridge([{"text": "hi"}])
    TT.check_cooldown = AsyncMock(return_value=False)
    assert await TT._process_account(acct, cfg, seen, "", "", "") == 0
    TT.check_cooldown = AsyncMock(return_value=True)
    # paused → skip
    _bridge([{"text": "hi"}])
    TT.is_thread_paused = AsyncMock(return_value=True)
    assert await TT._process_account(acct, cfg, seen, "", "", "") == 0


@pytest.mark.asyncio
async def test_tiktok_happy_and_fallback(monkeypatch):
    acct = _account({})
    b = _wire(TT, monkeypatch)
    b.get_tiktok_dm_conversations = AsyncMock(return_value={
        "conversations": [{"thread_id": "c1", "name": "Ada"}]})
    b.get_tiktok_dm_messages = AsyncMock(return_value={
        "messages": [{"text": "price?"}]})
    b.send_tiktok_dm_message = AsyncMock()

    seen: dict = {}
    n = await TT._process_account(
        acct, {"cooldown_seconds": 1}, seen, "t", "a", "u")
    assert n == 1
    b.send_tiktok_dm_message.assert_awaited_once_with("c1", "reply")
    assert seen["c1"] == "price?"
    assert TT.store_message_memory.await_count == 2

    # fallback_text path
    TT.generate_contextual_reply = AsyncMock(return_value="")
    n = await TT._process_account(
        acct, {"fallback_text": "FB"}, {"c2": ""}, "t", "a", "u")
    b.send_tiktok_dm_message.assert_awaited_with("c1", "FB")


# ── threads / linkedin / personal (clones) ────────────────────────────


def _wire_clone(mod, monkeypatch, bridge=None, platform=None,
                session_status="active"):
    """Same seam set for the threads clone (BrowserBridgeClient top-level)."""
    return _wire(mod, monkeypatch, bridge,
                 session_status=session_status)


@pytest.mark.asyncio
async def test_threads_process(monkeypatch):
    acct = _account({})
    cfg = {"cooldown_seconds": 1}

    def _bridge(msgs, convos=None):
        b = _wire(TH, monkeypatch)
        b.get_threads_dm_conversations = AsyncMock(return_value={
            "conversations": convos or [{"thread_id": "t1",
                                          "name": "User"}]})
        b.get_threads_dm_messages = AsyncMock(
            return_value={"messages": msgs})
        b.send_threads_dm_message = AsyncMock()
        return b

    # session inactive → 0
    _wire(TH, monkeypatch, session_status="inactive")
    assert await TH._process_account(acct, cfg, {}, "", "", "", "") == 0

    # convos without thread_id filtered entirely → 0
    _bridge([{"text": "hi"}], convos=[{"name": "NoID"}])
    assert await TH._process_account(acct, cfg, {}, "", "", "", "") == 0

    # sender contains account name → not inbound
    b = _bridge([{"text": "hi", "sender": "Cloudless"}])
    assert await TH._process_account(acct, cfg, {}, "", "", "", "") == 0

    # sender "me" → not inbound
    _bridge([{"text": "hi", "sender": "me"}])
    assert await TH._process_account(acct, cfg, {}, "", "", "", "") == 0

    # inbound from user → replied
    b = _bridge([{"text": "hi", "sender": "Ada"}])
    seen: dict = {}
    n = await TH._process_account(acct, cfg, seen, "", "", "", "")
    assert n == 1
    b.send_threads_dm_message.assert_awaited_once_with("t1", "reply")
    assert seen["t1"] == "hi"


@pytest.mark.asyncio
async def test_linkedin_process(monkeypatch):
    acct = _account({})
    cfg = {"cooldown_seconds": 1}

    sidecar = SimpleNamespace(
        health=AsyncMock(return_value={
            "rate_limited": False, "has_session": True}),
        get_conversations=AsyncMock(return_value={
            "conversations": [{"thread_id": "c1", "name": "Ada"}]}),
        get_thread_messages=AsyncMock(return_value={
            "messages": [{"text": "hi", "sender": "Ada"}]}),
        send_message=AsyncMock())
    monkeypatch.setattr(LI, "LinkedInSidecarClient",
                        lambda *a, **k: sidecar)

    # redis rate-limit cooldown active → 0
    import redis.asyncio as aioredis
    r = SimpleNamespace(get=AsyncMock(return_value="1"),
                        aclose=AsyncMock(),
                        setex=AsyncMock())
    monkeypatch.setattr(aioredis, "from_url", lambda *a, **k: r)
    assert await LI._process_account(
        acct, cfg, {}, "url", "t", "a", "u") == 0
    r.get = AsyncMock(return_value=None)

    # rate_limited circuit → 0
    sidecar.health = AsyncMock(return_value={
        "rate_limited": True, "rate_limit_until": "x"})
    assert await LI._process_account(
        acct, cfg, {}, "url", "t", "a", "u") == 0

    # no session → 0
    sidecar.health = AsyncMock(return_value={
        "rate_limited": False, "has_session": False})
    assert await LI._process_account(
        acct, cfg, {}, "url", "t", "a", "u") == 0

    # health raises → 0
    sidecar.health = AsyncMock(side_effect=RuntimeError("x"))
    assert await LI._process_account(
        acct, cfg, {}, "url", "t", "a", "u") == 0


@pytest.mark.asyncio
async def test_personal_process(monkeypatch):
    acct = _account({})
    cfg = {"cooldown_seconds": 1}
    import app.services.browser_bridge as BB
    import app.services.browser_orchestrator as BO

    bridge = SimpleNamespace(
        ensure_session=AsyncMock(return_value={"status": "active"}),
        get_personal_messenger_conversations_fast=AsyncMock(
            return_value={"conversations": []}),
        get_personal_messenger_messages_fast=AsyncMock(
            return_value={"messages": []}),
        send_personal_messenger_message=AsyncMock())
    monkeypatch.setattr(BB, "BrowserBridgeClient",
                        lambda *a, **k: bridge)

    class _BS:
        async def __aenter__(self):
            return bridge

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(BO, "browser_session", lambda *a, **k: _BS())
    for name in ("check_cooldown", "is_thread_paused"):
        setattr(PM, name, AsyncMock(return_value=name == "check_cooldown"))
    PM.is_thread_paused = AsyncMock(return_value=False)
    PM.detect_intent = AsyncMock(return_value="i")
    PM.retrieve_brand_context = AsyncMock(return_value="ctx")
    PM.generate_contextual_reply = AsyncMock(return_value="reply")
    PM.store_message_memory = AsyncMock()
    PM.set_cooldown = AsyncMock()

    # session inactive → 0
    bridge.ensure_session = AsyncMock(
        return_value={"status": "inactive", "message": "x"})
    assert await PM._process_account(
        acct, cfg, {}, "url", "t", "a", "u") == 0
    bridge.ensure_session = AsyncMock(
        return_value={"status": "active"})

    # read convo → only unread+thread_id
    bridge.get_personal_messenger_conversations_fast = AsyncMock(
        return_value={"conversations": [
            {"thread_id": "c1", "unread": True, "name": "Ada"},
            {"thread_id": "c2", "unread": False, "name": "Read"},
            {"unread": True},  # no thread_id
        ]})
    bridge.get_personal_messenger_messages_fast = AsyncMock(
        return_value={"messages": [{"text": "hi", "sender": "them"}]})

    seen: dict = {}
    n = await PM._process_account(acct, cfg, seen, "url", "t", "a", "u")
    assert n == 1
    # only c1 processed (read convo + no-id skipped)
    bridge.send_personal_messenger_message.assert_awaited_once()
    assert seen["c1"] == "hi" and "c2" not in seen


@pytest.mark.asyncio
async def test_linkedin_process_happy(monkeypatch):
    acct = _account({})
    cfg = {"cooldown_seconds": 1}

    sidecar = SimpleNamespace(
        health=AsyncMock(return_value={
            "rate_limited": False, "has_session": True}),
        get_conversations=AsyncMock(return_value={
            "conversations": [{"thread_id": "c1", "name": "Ada"}]}),
        get_thread_messages=AsyncMock(return_value={
            "messages": [{"text": "hi", "sender": "Ada"}]}),
        send_message=AsyncMock())
    monkeypatch.setattr(LI, "LinkedInSidecarClient",
                        lambda *a, **k: sidecar)
    import redis.asyncio as aioredis
    r = SimpleNamespace(get=AsyncMock(return_value=None),
                        aclose=AsyncMock(), setex=AsyncMock())
    monkeypatch.setattr(aioredis, "from_url", lambda *a, **k: r)
    for name in ("check_cooldown",):
        setattr(LI, name, AsyncMock(return_value=True))
    LI.is_thread_paused = AsyncMock(return_value=False)
    LI.detect_intent = AsyncMock(return_value="i")
    LI.retrieve_brand_context = AsyncMock(return_value="ctx")
    LI.generate_contextual_reply = AsyncMock(return_value="reply")
    LI.store_message_memory = AsyncMock()
    LI.set_cooldown = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    seen: dict = {}
    n = await LI._process_account(acct, cfg, seen, "url", "t", "a", "u")
    assert n == 1
    sidecar.send_message.assert_awaited_once_with("c1", "reply")
    assert seen["c1"] == "hi"
    assert LI.store_message_memory.await_count == 2


@pytest.mark.asyncio
async def test_linkedin_429_cooldown(monkeypatch):
    acct = _account({})
    sidecar = SimpleNamespace(
        health=AsyncMock(return_value={
            "rate_limited": False, "has_session": True}),
        get_conversations=AsyncMock(
            side_effect=LI.LinkedInSidecarError(429, "RATE_LIMITED")))
    monkeypatch.setattr(LI, "LinkedInSidecarClient",
                        lambda *a, **k: sidecar)
    import redis.asyncio as aioredis
    r = SimpleNamespace(get=AsyncMock(return_value=None),
                        aclose=AsyncMock(), setex=AsyncMock())
    monkeypatch.setattr(aioredis, "from_url", lambda *a, **k: r)
    n = await LI._process_account(acct, {}, {}, "url", "t", "a", "u")
    assert n == 0
    # 6h backoff written to redis
    r.setex.assert_awaited_once()
    assert r.setex.await_args.args[1] == 21600

    # non-429 sidecar error → 0, no setex
    r.setex = AsyncMock()
    sidecar.get_conversations = AsyncMock(
        side_effect=LI.LinkedInSidecarError(500, "boom"))
    assert await LI._process_account(
        acct, {}, {}, "url", "t", "a", "u") == 0
    r.setex.assert_not_awaited()


@pytest.mark.asyncio
async def test_clone_pollers(monkeypatch):
    """threads + linkedin + personal poll loops share the same shape."""

    class _DB:
        def __init__(self, accounts):
            self.accounts = accounts
            self.commit = AsyncMock()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, *a):
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: self.accounts))

    import sqlalchemy.orm.attributes as SOA
    monkeypatch.setattr(SOA, "flag_modified", lambda *a, **k: None)

    for mod, key, poller, proc_name in (
            (TH, "threads_auto_reply",
             TH._poll_threads_messenger_async, "_process_account"),
            (LI, "linkedin_auto_reply",
             LI._poll_linkedin_messenger_async, "_process_account"),
            (PM, "personal_messenger_auto_reply",
             PM._poll_personal_messenger_async, "_process_account")):
        acct = _account({key: {"enabled": True}})
        monkeypatch.setattr(mod, "_worker_db", lambda: _DB([acct]))
        monkeypatch.setattr(mod, proc_name, AsyncMock(return_value=3))
        out = await poller()
        assert out["accounts_checked"] == 1 and out["replies_sent"] == 3


@pytest.mark.asyncio
async def test_threads_convo_guards(monkeypatch):
    acct = _account({})
    cfg = {"cooldown_seconds": 1}

    def _bridge(msgs):
        b = _wire(TH, monkeypatch)
        b.get_threads_dm_conversations = AsyncMock(return_value={
            "conversations": [{"thread_id": "t1", "name": "U"}]})
        b.get_threads_dm_messages = AsyncMock(
            return_value={"messages": msgs})
        b.send_threads_dm_message = AsyncMock()
        return b

    # empty messages / no inbound / seen / cooldown / paused
    _bridge([])
    assert await TH._process_account(acct, cfg, {}, "", "", "", "") == 0
    _bridge([{"text": "", "sender": "Ada"}])
    assert await TH._process_account(acct, cfg, {}, "", "", "", "") == 0
    _bridge([{"text": "hi", "sender": "Ada"}])
    assert await TH._process_account(acct, cfg, {"t1": "hi"},
                                     "", "", "", "") == 0
    _bridge([{"text": "hi", "sender": "Ada"}])
    TH.check_cooldown = AsyncMock(return_value=False)
    assert await TH._process_account(acct, cfg, {}, "", "", "", "") == 0
    TH.check_cooldown = AsyncMock(return_value=True)
    _bridge([{"text": "hi", "sender": "Ada"}])
    TH.is_thread_paused = AsyncMock(return_value=True)
    assert await TH._process_account(acct, cfg, {}, "", "", "", "") == 0


# ── extra threads coverage from #518 ─────────────────────────────────
_ARGS = ("bridge-url", "cf-tok", "cf-acc", "dmr")


@pytest.mark.asyncio
async def test_threads_poller(monkeypatch):
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

    monkeypatch.setattr(TH, "_worker_db", lambda: _DB([]))
    out = await TH._poll_threads_messenger_async()
    assert out == {"accounts_checked": 0, "replies_sent": 0, "errors": 0}

    acct_on = _account({"threads_auto_reply": {"enabled": True}, "threads_messenger_seen": {}})
    acct_off = _account({"threads_auto_reply": {"enabled": False}})
    acct_err = _account({"threads_auto_reply": {"enabled": True}})
    monkeypatch.setattr(TH, "_worker_db", lambda: _DB([acct_off, acct_on, acct_err]))
    monkeypatch.setattr(TH, "_process_account", AsyncMock(side_effect=[1, RuntimeError("x")]))
    import sqlalchemy.orm.attributes as SOA

    monkeypatch.setattr(SOA, "flag_modified", lambda *a, **k: None)

    out = await TH._poll_threads_messenger_async()
    assert out["accounts_checked"] == 2
    assert out["replies_sent"] == 1 and out["errors"] == 1
    assert "threads_messenger_last_checked" in acct_on.meta_data


@pytest.mark.asyncio
async def test_threads_process_guards(monkeypatch):
    acct = _account({})
    cfg = {"cooldown_seconds": 300}
    seen: dict = {}

    _wire(TH, monkeypatch, session_status="inactive")
    assert await TH._process_account(acct, cfg, seen, *_ARGS) == 0

    b = _wire(TH, monkeypatch)
    b.ensure_session = AsyncMock(side_effect=RuntimeError("x"))
    assert await TH._process_account(acct, cfg, seen, *_ARGS) == 0

    b = _wire(TH, monkeypatch)
    b.get_threads_dm_conversations = AsyncMock(side_effect=RuntimeError("x"))
    assert await TH._process_account(acct, cfg, seen, *_ARGS) == 0

    b = _wire(TH, monkeypatch)
    b.get_threads_dm_conversations = AsyncMock(return_value={"conversations": []})
    assert await TH._process_account(acct, cfg, seen, *_ARGS) == 0


@pytest.mark.asyncio
async def test_threads_happy_and_fallback(monkeypatch):
    acct = _account({})
    b = _wire(TH, monkeypatch)
    b.get_threads_dm_conversations = AsyncMock(return_value={"conversations": [{"thread_id": "t1", "name": "Ada"}, {"thread_id": "t2", "name": "Bad"}]})
    b.get_threads_dm_messages = AsyncMock(side_effect=[{"messages": [{"text": "price?", "sender": "them"}]}, RuntimeError("msg fetch boom")])
    b.send_threads_dm_message = AsyncMock()

    seen: dict = {}
    n = await TH._process_account(acct, {"cooldown_seconds": 1}, seen, *_ARGS)
    assert n == 1
    b.send_threads_dm_message.assert_awaited_once_with("t1", "reply")
    assert seen == {"t1": "price?"}
    assert TH.store_message_memory.await_count == 2
    TH.set_cooldown.assert_awaited_once()

    b2 = _wire(TH, monkeypatch)
    TH.generate_contextual_reply = AsyncMock(return_value="")
    b2.get_threads_dm_conversations = AsyncMock(return_value={"conversations": [{"thread_id": "t1"}]})
    b2.get_threads_dm_messages = AsyncMock(return_value={"messages": [{"text": "yo", "sender": "them"}]})
    b2.send_threads_dm_message = AsyncMock()
    await TH._process_account(acct, {"fallback_text": "FBTXT"}, {}, *_ARGS)
    b2.send_threads_dm_message.assert_awaited_once_with("t1", "FBTXT")


def test_threads_run_async():
    async def _coro():
        return 7

    assert TH._run_async(_coro()) == 7


@pytest.mark.asyncio
async def test_threads_running_loop_and_wrapper(monkeypatch):
    async def _coro():
        return 9

    assert TH._run_async(_coro()) == 9

    async def _stub():
        return {"ok": 1}

    monkeypatch.setattr(TH, "_poll_threads_messenger_async", _stub)
    assert TH.poll_threads_messenger() == {"ok": 1}

    async def _boom():
        raise RuntimeError("inner")

    monkeypatch.setattr(TH, "_poll_threads_messenger_async", _boom)
    with pytest.raises(RuntimeError):
        TH.poll_threads_messenger()


@pytest.mark.asyncio
async def test_threads_bridge_error_in_loop(monkeypatch):
    from app.services.browser_bridge import BrowserBridgeError

    acct = _account({})
    b = _wire(TH, monkeypatch)
    b.get_threads_dm_conversations = AsyncMock(return_value={"conversations": [{"thread_id": "t1"}]})
    b.get_threads_dm_messages = AsyncMock(side_effect=BrowserBridgeError(500, "bridge down"))
    b.send_threads_dm_message = AsyncMock()
    assert await TH._process_account(acct, {}, {}, *_ARGS) == 0
    b.send_threads_dm_message.assert_not_awaited()
