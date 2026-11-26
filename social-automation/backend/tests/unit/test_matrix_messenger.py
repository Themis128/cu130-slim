"""Unit tests for the matrix_messenger worker task (mautrix-meta lane)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.worker.tasks.matrix_messenger as MM


def _account(meta: dict):
    return SimpleNamespace(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        account_name="Themis",
        meta_data=meta,
    )


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


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _Client:
    """Fake httpx.AsyncClient recording calls and serving canned /sync."""

    def __init__(self, payload=None, *, get_exc=None, put_exc=None, post_exc=None):
        self.payload = payload or {}
        self.get_exc = get_exc
        self.put_exc = put_exc
        self.post_exc = post_exc
        self.gets = []
        self.puts = []
        self.posts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        self.gets.append((url, kw))
        if self.get_exc:
            raise self.get_exc
        return _Resp(self.payload)

    async def put(self, url, **kw):
        self.puts.append((url, kw))
        if self.put_exc:
            raise self.put_exc
        return _Resp({})

    async def post(self, url, **kw):
        self.posts.append((url, kw))
        if self.post_exc:
            raise self.post_exc
        return _Resp({})


def _msg(sender, body="hi", event_id="$e1"):
    return {
        "type": "m.room.message",
        "sender": sender,
        "event_id": event_id,
        "content": {"msgtype": "m.text", "body": body},
    }


def _wire(monkeypatch, accounts, client, *, token="tok", since="s1"):
    """Patch all external seams; returns the fake client."""
    monkeypatch.setattr(MM, "_matrix_token", AsyncMock(return_value=token))
    monkeypatch.setattr(MM, "_sync_since", AsyncMock(return_value=since))
    monkeypatch.setattr(MM, "_set_sync_since", AsyncMock())
    monkeypatch.setattr(MM, "_worker_db", lambda: _DB(accounts))
    monkeypatch.setattr(MM.httpx, "AsyncClient", lambda *a, **k: client)
    monkeypatch.setattr(MM, "check_cooldown", AsyncMock(return_value=True))
    monkeypatch.setattr(MM, "is_thread_paused", AsyncMock(return_value=False))
    monkeypatch.setattr(MM, "detect_intent", AsyncMock(return_value="i"))
    monkeypatch.setattr(MM, "retrieve_brand_context", AsyncMock(return_value="ctx"))
    monkeypatch.setattr(MM, "generate_contextual_reply", AsyncMock(return_value="reply!"))
    monkeypatch.setattr(MM, "set_cooldown", AsyncMock())
    monkeypatch.setattr(MM, "store_message_memory", AsyncMock())
    monkeypatch.setattr(MM, "flag_modified", lambda *a, **k: None)
    return client


# ── token / redis helpers ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_matrix_token_reads_secret_store(monkeypatch):
    import app.services.secret_store as mod

    store = SimpleNamespace(get=AsyncMock(return_value="sekrit"))
    monkeypatch.setattr(mod, "secret_store", store)
    assert await MM._matrix_token() == "sekrit"
    store.get = AsyncMock(return_value=None)
    assert await MM._matrix_token() == ""


@pytest.mark.asyncio
async def test_sync_since_redis_roundtrip(monkeypatch):
    import redis.asyncio as aioredis

    store = {}

    class _R:
        async def get(self, k):
            return store.get(k)

        async def set(self, k, v):
            store[k] = v.encode() if isinstance(v, str) else v

        async def aclose(self):
            pass

    monkeypatch.setattr(aioredis, "from_url", lambda url: _R())
    assert await MM._sync_since() == ""
    await MM._set_sync_since("tok123")
    assert await MM._sync_since() == "tok123"


# ── early exits ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_token_returns_zeros(monkeypatch):
    _wire(monkeypatch, [], _Client(), token="")
    out = await MM._poll_matrix_messenger_async()
    assert out == {"accounts_checked": 0, "rooms_seen": 0, "replies_sent": 0, "errors": 0}


@pytest.mark.asyncio
async def test_no_enabled_accounts(monkeypatch):
    acct = _account({"personal_messenger_auto_reply": {"enabled": False}})
    _wire(monkeypatch, [acct], _Client(), since="s1")
    out = await MM._poll_matrix_messenger_async()
    assert out["replies_sent"] == 0
    assert out["accounts_checked"] == 0


@pytest.mark.asyncio
async def test_sync_failure_counts_error(monkeypatch):
    acct = _account({"personal_messenger_auto_reply": {"enabled": True}})
    _wire(monkeypatch, [acct], _Client(get_exc=RuntimeError("down")), since="s1")
    out = await MM._poll_matrix_messenger_async()
    assert out["errors"] == 1
    assert out["accounts_checked"] == 1


@pytest.mark.asyncio
async def test_bootstrap_stores_token_no_replies(monkeypatch):
    acct = _account({"personal_messenger_auto_reply": {"enabled": True}})
    client = _Client({"next_batch": "nb1", "rooms": {"join": {"!r:x": {"timeline": {"events": [_msg("@facebook_a:x")]}}}}})
    _wire(monkeypatch, [acct], client, since="")
    out = await MM._poll_matrix_messenger_async()
    MM._set_sync_since.assert_awaited_with("nb1")
    assert out["replies_sent"] == 0
    assert out["rooms_seen"] == 0  # bootstrap returns before room scan


# ── room scan paths ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reply_happy_path(monkeypatch):
    acct = _account({"personal_messenger_auto_reply": {"enabled": True, "cooldown_seconds": 60}})
    payload = {
        "next_batch": "nb2",
        "rooms": {"join": {"!room1:x": {"timeline": {"events": [_msg("@facebook_ghost:x", "hello", "$e9")]}}}},
    }
    client = _wire(monkeypatch, [acct], _Client(payload))
    out = await MM._poll_matrix_messenger_async()
    assert out["replies_sent"] == 1
    assert out["rooms_seen"] == 1
    send_calls = [u for u, _ in client.puts if "/send/m.room.message/" in u]
    typing_calls = [u for u, _ in client.puts if "/typing/" in u]
    assert len(send_calls) == 1 and len(typing_calls) == 1
    assert acct.meta_data["matrix_messenger_seen"]["!room1:x"] == "$e9"
    assert "matrix_messenger_last_checked" in acct.meta_data
    assert MM._set_sync_since.await_args.args[0] == "nb2"


@pytest.mark.asyncio
async def test_skips_non_ghost_and_seen_and_empty(monkeypatch):
    acct = _account({"personal_messenger_auto_reply": {"enabled": True}, "matrix_messenger_seen": {"!seen:x": "$e1"}})
    payload = {
        "next_batch": "nb",
        "rooms": {
            "join": {
                "!own:x": {"timeline": {"events": [_msg("@me:x")]}},          # our own msg
                "!seen:x": {"timeline": {"events": [_msg("@facebook_g:x", "hi", "$e1")]}},  # already seen
                "!empty:x": {"timeline": {"events": [_msg("@facebook_g:x", "  ", "$e2")]}},  # empty body
                "!nomsg:x": {"timeline": {"events": [{"type": "m.room.member"}]}},           # no msg event
            }
        },
    }
    _wire(monkeypatch, [acct], _Client(payload))
    out = await MM._poll_matrix_messenger_async()
    assert out["replies_sent"] == 0
    assert out["rooms_seen"] == 3  # own, seen, empty all counted; nomsg isn't


@pytest.mark.asyncio
async def test_cooldown_paused_and_no_reply_gates(monkeypatch):
    acct = _account({"personal_messenger_auto_reply": {"enabled": True, "cooldown_seconds": 10}})
    def ghost(eid):
        return {"timeline": {"events": [_msg("@facebook_g:x", "hi", eid)]}}

    payload = {
        "next_batch": "nb",
        "rooms": {"join": {"!cool:x": ghost("$a"), "!pause:x": ghost("$b"), "!gen:x": ghost("$c")}},
    }
    _wire(monkeypatch, [acct], _Client(payload))
    MM.check_cooldown = AsyncMock(side_effect=lambda aid, tid, s: tid != "!cool:x")
    MM.is_thread_paused = AsyncMock(side_effect=lambda aid, tid: tid == "!pause:x")
    MM.generate_contextual_reply = AsyncMock(return_value=None)
    out = await MM._poll_matrix_messenger_async()
    assert out["replies_sent"] == 0
    assert out["rooms_seen"] == 3


@pytest.mark.asyncio
async def test_room_exception_and_typing_failure(monkeypatch):
    acct = _account({"personal_messenger_auto_reply": {"enabled": True}})
    payload = {
        "next_batch": "nb",
        "rooms": {
            "join": {
                "!bad:x": {"timeline": {"events": [_msg("@facebook_g:x", "boom", "$e1")]}},
                "!ok:x": {"timeline": {"events": [_msg("@facebook_g:x", "fine", "$e2")]}},
            }
        },
    }
    _wire(monkeypatch, [acct], _Client(payload, put_exc=RuntimeError("put fail")))
    # first room explodes on cooldown check; second hits typing/send put failure
    MM.check_cooldown = AsyncMock(side_effect=lambda aid, tid, s: (_ for _ in ()).throw(RuntimeError("cd")) if tid == "!bad:x" else True)
    out = await MM._poll_matrix_messenger_async()
    assert out["errors"] == 2  # !bad cooldown raise + !ok send-put raise
    assert out["rooms_seen"] == 2
    assert out["replies_sent"] == 0


@pytest.mark.asyncio
async def test_multiple_enabled_warns_uses_first(monkeypatch):
    a1 = _account({"personal_messenger_auto_reply": {"enabled": True}})
    a2 = _account({"personal_messenger_auto_reply": {"enabled": True}})
    payload = {"next_batch": "nb", "rooms": {"join": {"!r:x": {"timeline": {"events": [_msg("@facebook_g:x")]}}}}}
    _wire(monkeypatch, [a1, a2], _Client(payload))
    out = await MM._poll_matrix_messenger_async()
    assert out["accounts_checked"] == 1
    assert out["replies_sent"] == 1
    assert a1.meta_data["matrix_messenger_seen"] and "matrix_messenger_seen" not in a2.meta_data


@pytest.mark.asyncio
async def test_invite_rooms_auto_joined(monkeypatch):
    acct = _account({"personal_messenger_auto_reply": {"enabled": True}})
    payload = {
        "next_batch": "nb",
        "rooms": {
            "invite": {"!newportal:x": {}, "!portal2:x": {}},
            "join": {"!room1:x": {"timeline": {"events": [_msg("@facebook_g:x", "hi", "$e1")]}}},
        },
    }
    client = _wire(monkeypatch, [acct], _Client(payload))
    out = await MM._poll_matrix_messenger_async()
    assert out["rooms_joined"] == 2
    join_calls = sorted(u for u, _ in client.posts if u.endswith("/join"))
    assert join_calls == [
        "/_matrix/client/v3/rooms/!newportal:x/join",
        "/_matrix/client/v3/rooms/!portal2:x/join",
    ]


@pytest.mark.asyncio
async def test_invite_join_failure_is_nonfatal(monkeypatch):
    acct = _account({"personal_messenger_auto_reply": {"enabled": True}})
    payload = {"next_batch": "nb", "rooms": {"invite": {"!p:x": {}}, "join": {}}}
    _wire(monkeypatch, [acct], _Client(payload, post_exc=RuntimeError("no join")))
    out = await MM._poll_matrix_messenger_async()
    assert out.get("rooms_joined", 0) == 0
    assert out["errors"] == 0


# ── sync wrapper / _run_async ─────────────────────────────────────────


def test_poll_task_wrapper(monkeypatch):
    def _fake(coro):
        coro.close()
        return {"ok": True}

    monkeypatch.setattr(MM, "_run_async", _fake)
    assert MM.poll_matrix_messenger() == {"ok": True}


@pytest.mark.asyncio
async def test_run_async_uses_thread_when_loop_running(monkeypatch):
    async def _coro():
        return 42

    assert MM._run_async(_coro()) == 42

    async def _boom():
        raise ValueError("nope")

    with pytest.raises(ValueError):
        MM._run_async(_boom())


def test_run_async_falls_back_without_loop(monkeypatch):
    import asyncio

    def _fallback(c):
        c.close()
        return "fallback"

    monkeypatch.setattr(asyncio, "get_event_loop", lambda: (_ for _ in ()).throw(RuntimeError("no loop")))
    monkeypatch.setattr(MM, "run_async", _fallback)

    async def _coro():
        return 1

    assert MM._run_async(_coro()) == "fallback"
