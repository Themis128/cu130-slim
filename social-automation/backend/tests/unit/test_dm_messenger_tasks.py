"""Coverage for app.worker.tasks.{tiktok,twitter}_messenger.

Poll wrapper + _run_async loop paths, the async account loop (enabled
filter, commit, error counting), and _process_account's session/convo/
seen/cooldown/handoff/reply ladder. Browser bridge + chatbot helpers are
faked at module boundaries; all loop logic runs for real.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.worker.tasks.tiktok_messenger as TM
import app.worker.tasks.twitter_messenger as WM


class _ACM:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *a):
        return False


class _Res:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._rows))


class _DB:
    def __init__(self, queues=None):
        self.queues = list(queues or [])
        self.commits = 0

    async def execute(self, stmt):
        return _Res(self.queues.pop(0) if self.queues else [])

    async def commit(self):
        self.commits += 1


def _account(meta, account_id="a1"):
    return SimpleNamespace(
        id=account_id,
        display_name="Acc",
        username="acc",
        meta_data=meta,
        team_id="t1",
    )


class _FakeBridgeSession:
    """Async-CM wrapper yielding a scripted bridge for browser_session()."""

    def __init__(self, bridge):
        self._b = bridge

    async def __aenter__(self):
        return self._b

    async def __aexit__(self, *a):
        return False


def _install_module_fakes(monkeypatch, mod, bridge):
    """Wire a fake BrowserBridgeClient + browser_session + chatbot helpers."""
    monkeypatch.setattr(mod, "BrowserBridgeClient", lambda *a, **k: bridge)
    monkeypatch.setattr(mod, "browser_session", lambda platform, b: _FakeBridgeSession(b))
    for name in ("check_cooldown", "set_cooldown", "is_thread_paused", "store_message_memory"):
        if hasattr(mod, name):
            monkeypatch.setattr(mod, name, AsyncMock(return_value=(name == "check_cooldown" and True) or (name == "is_thread_paused" and False) or None))
    monkeypatch.setattr(mod, "check_cooldown", AsyncMock(return_value=True))
    monkeypatch.setattr(mod, "is_thread_paused", AsyncMock(return_value=False))
    monkeypatch.setattr(mod, "detect_intent", AsyncMock(return_value="question"))
    monkeypatch.setattr(mod, "retrieve_brand_context", AsyncMock(return_value="ctx"))
    monkeypatch.setattr(mod, "generate_contextual_reply", AsyncMock(return_value="reply text"))
    monkeypatch.setattr(mod, "set_cooldown", AsyncMock())
    monkeypatch.setattr(mod, "store_message_memory", AsyncMock())
    monkeypatch.setattr(mod, "mark_message_as_replied", AsyncMock(), raising=False)


def _bridge(mod, convos=None, messages=None):
    convo_getter = {
        TM: "get_tiktok_dm_conversations",
        WM: "get_twitter_dm_conversations",
    }[mod]
    msg_getter = {
        TM: "get_tiktok_dm_messages",
        WM: "get_twitter_dm_messages",
    }[mod]
    send = {
        TM: "send_tiktok_dm_message",
        WM: "send_twitter_dm_message",
    }[mod]
    bridge = SimpleNamespace(
        ensure_session=AsyncMock(return_value={"status": "active"}),
        **{
            convo_getter: AsyncMock(return_value={"conversations": convos or []}),
            msg_getter: AsyncMock(return_value={"messages": messages or []}),
            send: AsyncMock(),
        },
    )
    return bridge, convo_getter, msg_getter, send


# ---------------------------------------------------------------------------
# poll wrapper + _run_async
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mod,task", [(TM, "poll_tiktok_messenger"), (WM, "poll_twitter_messenger")])
def test_poll_wrapper(monkeypatch, mod, task):
    def runner(coro):
        coro.close()
        return {"accounts_checked": 1}

    monkeypatch.setattr(mod, "_run_async", runner)
    assert getattr(mod, task)() == {"accounts_checked": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("mod", [TM, WM])
async def test_run_async_thread_path(mod):
    async def coro():
        return {"v": 1}

    assert mod._run_async(coro()) == {"v": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("mod", [TM, WM])
async def test_run_async_thread_error(mod):
    async def coro():
        raise RuntimeError("inner")

    with pytest.raises(RuntimeError):
        mod._run_async(coro())


# ---------------------------------------------------------------------------
# async poll loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mod,flag,seenkey",
    [
        (TM, "tiktok_auto_reply", "tiktok_messenger_seen"),
        (WM, "twitter_auto_reply", "twitter_messenger_seen"),
    ],
)
async def test_poll_async_loop(monkeypatch, mod, flag, seenkey):
    enabled = _account({"platform_auto": True, flag: {"enabled": True}, seenkey: {}})
    disabled = _account({flag: {"enabled": False}}, account_id="a2")
    db = _DB([[enabled, disabled]])
    monkeypatch.setattr(mod, "_worker_db", lambda: _ACM(db))
    monkeypatch.setattr(mod, "_process_account", AsyncMock(return_value=2))
    import sqlalchemy.orm.attributes as orm_attrs

    monkeypatch.setattr(orm_attrs, "flag_modified", lambda *a, **k: None)
    stats = await mod._poll_tiktok_messenger_async() if mod is TM else await mod._poll_twitter_messenger_async()
    assert stats["accounts_checked"] == 1
    assert stats["replies_sent"] == 2
    assert db.commits == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mod", [TM, WM])
async def test_poll_async_process_error_counts(monkeypatch, mod):
    flag = "tiktok_auto_reply" if mod is TM else "twitter_auto_reply"
    acc = _account({flag: {"enabled": True}})
    db = _DB([[acc]])
    monkeypatch.setattr(mod, "_worker_db", lambda: _ACM(db))
    monkeypatch.setattr(mod, "_process_account", AsyncMock(side_effect=RuntimeError("bridge dead")))
    stats = await (mod._poll_tiktok_messenger_async() if mod is TM else mod._poll_twitter_messenger_async())
    assert stats["errors"] == 1
    assert stats["replies_sent"] == 0


# ---------------------------------------------------------------------------
# _process_account ladder (tiktok)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tiktok_session_inactive_and_bridge_fail(monkeypatch):
    acc = _account({})
    bridge = SimpleNamespace(ensure_session=AsyncMock(return_value={"status": "expired", "message": "m"}))
    _install_module_fakes(monkeypatch, TM, bridge)
    assert await TM._process_account(acc, {}, {}, "", "", "") == 0

    bridge.ensure_session = AsyncMock(side_effect=RuntimeError("down"))
    assert await TM._process_account(acc, {}, {}, "", "", "") == 0


@pytest.mark.asyncio
async def test_tiktok_convo_fetch_error_and_empty(monkeypatch):
    acc = _account({})
    bridge, convo_getter, _, _ = _bridge(TM)
    getattr(bridge, convo_getter).side_effect = RuntimeError("nav fail")
    _install_module_fakes(monkeypatch, TM, bridge)
    assert await TM._process_account(acc, {}, {}, "", "", "") == 0

    getattr(bridge, convo_getter).side_effect = None
    getattr(bridge, convo_getter).return_value = {"conversations": []}
    assert await TM._process_account(acc, {}, {}, "", "", "") == 0


@pytest.mark.asyncio
async def test_tiktok_full_reply_flow(monkeypatch):
    acc = _account({})
    convos = [{"thread_id": "c1", "name": "Fan"}, {"thread_id": "", "name": "NoId"}]
    messages = [
        {"text": "our reply", "sender": "me"},
        {"text": "hello from fan", "sender": "them"},
    ]
    bridge, convo_getter, msg_getter, send = _bridge(TM, convos, messages)
    _install_module_fakes(monkeypatch, TM, bridge)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    seen = {}
    n = await TM._process_account(acc, {"cooldown_seconds": 10}, seen, "tok", "acct", "dmr")
    assert n == 1
    assert seen["c1"] == "hello from fan"
    getattr(bridge, send).assert_awaited()
    TM.set_cooldown.assert_awaited()


@pytest.mark.asyncio
async def test_tiktok_skip_paths(monkeypatch):
    acc = _account({})
    convos = [{"thread_id": "c1"}]
    messages = [{"text": "hi", "sender": "them"}]

    # already-seen → skip
    bridge, _, _, _ = _bridge(TM, convos, messages)
    _install_module_fakes(monkeypatch, TM, bridge)
    n = await TM._process_account(acc, {}, {"c1": "hi"}, "", "", "")
    assert n == 0

    # cooldown → skip
    bridge, _, _, _ = _bridge(TM, convos, messages)
    _install_module_fakes(monkeypatch, TM, bridge)
    TM.check_cooldown = AsyncMock(return_value=False)
    assert await TM._process_account(acc, {}, {}, "", "", "") == 0

    # handoff paused → skip
    bridge, _, _, _ = _bridge(TM, convos, messages)
    _install_module_fakes(monkeypatch, TM, bridge)
    TM.is_thread_paused = AsyncMock(return_value=True)
    assert await TM._process_account(acc, {}, {}, "", "", "") == 0

    # no messages in convo → skip; empty text → skip
    bridge, _, msg_getter, _ = _bridge(TM, convos, [])
    _install_module_fakes(monkeypatch, TM, bridge)
    assert await TM._process_account(acc, {}, {}, "", "", "") == 0

    bridge, _, msg_getter, _ = _bridge(TM, convos, [{"text": "  ", "sender": "them"}])
    _install_module_fakes(monkeypatch, TM, bridge)
    assert await TM._process_account(acc, {}, {}, "", "", "") == 0


@pytest.mark.asyncio
async def test_tiktok_generate_empty_uses_fallback(monkeypatch):
    acc = _account({})
    bridge, _, _, send = _bridge(TM, [{"thread_id": "c1"}], [{"text": "hi"}])
    _install_module_fakes(monkeypatch, TM, bridge)
    TM.generate_contextual_reply = AsyncMock(return_value="")
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    n = await TM._process_account(acc, {"fallback_text": "FALLBACK"}, {}, "", "", "")
    assert n == 1
    assert getattr(bridge, send).call_args[0][1] == "FALLBACK"


# ---------------------------------------------------------------------------
# _process_account ladder (twitter) — mirrored but no sender-me break
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_twitter_session_inactive_bridge_fail_and_empty(monkeypatch):
    acc = _account({})
    bridge = SimpleNamespace(ensure_session=AsyncMock(return_value={"status": "expired"}))
    _install_module_fakes(monkeypatch, WM, bridge)
    assert await WM._process_account(acc, {}, {}, "", "", "") == 0
    bridge.ensure_session = AsyncMock(side_effect=RuntimeError("down"))
    assert await WM._process_account(acc, {}, {}, "", "", "") == 0


@pytest.mark.asyncio
async def test_twitter_full_reply_flow(monkeypatch):
    acc = _account({})
    convos = [{"thread_id": "c9", "name": "Fan"}]
    messages = [{"text": "hey twitter", "sender": "them"}]
    bridge, _, _, send = _bridge(WM, convos, messages)
    _install_module_fakes(monkeypatch, WM, bridge)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    seen = {}
    n = await WM._process_account(acc, {"cooldown_seconds": 5}, seen, "", "", "")
    assert n == 1
    assert seen["c9"] == "hey twitter"


@pytest.mark.asyncio
async def test_twitter_skip_seen_cooldown_paused(monkeypatch):
    acc = _account({})
    convos = [{"thread_id": "c1"}]
    messages = [{"text": "hi"}]

    bridge, _, _, _ = _bridge(WM, convos, messages)
    _install_module_fakes(monkeypatch, WM, bridge)
    assert await WM._process_account(acc, {}, {"c1": "hi"}, "", "", "") == 0

    bridge, _, _, _ = _bridge(WM, convos, messages)
    _install_module_fakes(monkeypatch, WM, bridge)
    WM.check_cooldown = AsyncMock(return_value=False)
    assert await WM._process_account(acc, {}, {}, "", "", "") == 0

    bridge, _, _, _ = _bridge(WM, convos, messages)
    _install_module_fakes(monkeypatch, WM, bridge)
    WM.is_thread_paused = AsyncMock(return_value=True)
    assert await WM._process_account(acc, {}, {}, "", "", "") == 0


@pytest.mark.asyncio
async def test_twitter_remaining_skip_and_error_paths(monkeypatch):
    acc = _account({})

    # convo fetch throws → 0
    bridge, convo_getter, _, _ = _bridge(WM)
    getattr(bridge, convo_getter).side_effect = RuntimeError("nav fail")
    _install_module_fakes(monkeypatch, WM, bridge)
    assert await WM._process_account(acc, {}, {}, "", "", "") == 0

    # empty conversations → 0
    getattr(bridge, convo_getter).side_effect = None
    getattr(bridge, convo_getter).return_value = {"conversations": []}
    assert await WM._process_account(acc, {}, {}, "", "", "") == 0

    # convo with no thread_id + empty messages + whitespace text → all skipped
    bridge, _, msg_getter, _ = _bridge(WM, [{"thread_id": ""}, {"thread_id": "c1"}], [])
    _install_module_fakes(monkeypatch, WM, bridge)
    assert await WM._process_account(acc, {}, {}, "", "", "") == 0

    bridge, _, msg_getter, _ = _bridge(WM, [{"thread_id": "c1"}], [{"text": "  "}])
    _install_module_fakes(monkeypatch, WM, bridge)
    assert await WM._process_account(acc, {}, {}, "", "", "") == 0

    # per-convo exception is logged, not fatal → still 0, no crash
    bridge, _, msg_getter, _ = _bridge(WM, [{"thread_id": "c1"}], None)
    getattr(bridge, msg_getter).side_effect = RuntimeError("msgs dead")
    _install_module_fakes(monkeypatch, WM, bridge)
    assert await WM._process_account(acc, {}, {}, "", "", "") == 0


@pytest.mark.asyncio
async def test_tiktok_sender_me_break_and_convo_error(monkeypatch):
    acc = _account({})

    # last message is mine → no inbound → skip (covers sender=='me' break)
    bridge, _, _, _ = _bridge(TM, [{"thread_id": "c1"}], [{"text": "mine", "sender": "me"}])
    _install_module_fakes(monkeypatch, TM, bridge)
    assert await TM._process_account(acc, {}, {}, "", "", "") == 0

    # messages fetch throws per-convo → logged, not fatal
    bridge, _, msg_getter, _ = _bridge(TM, [{"thread_id": "c1"}], None)
    getattr(bridge, msg_getter).side_effect = RuntimeError("dead")
    _install_module_fakes(monkeypatch, TM, bridge)
    assert await TM._process_account(acc, {}, {}, "", "", "") == 0
