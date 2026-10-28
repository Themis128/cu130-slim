"""Unit tests for app/services/viber_chatbot.py."""

from __future__ import annotations

import sys
import uuid
from unittest.mock import AsyncMock

import pytest

import app.services.viber_chatbot as VC


class _Redis:
    def __init__(self, *, exists: int = 0, raises: bool = False):
        self._exists = exists
        self._raises = raises
        self.sets: list[tuple] = []
        self.deleted: list[str] = []
        self.closed = False

    async def exists(self, key):
        if self._raises:
            raise RuntimeError("redis down")
        return self._exists

    async def set(self, key, val, ex=None):
        if self._raises:
            raise RuntimeError("redis down")
        self.sets.append((key, val, ex))

    async def delete(self, key):
        self.deleted.append(key)

    async def aclose(self):
        self.closed = True


@pytest.fixture
def no_redis(monkeypatch):
    monkeypatch.setattr(VC, "_redis", lambda: None)


@pytest.mark.asyncio
async def test_cooldown_paths(monkeypatch):
    # no redis → allow
    monkeypatch.setattr(VC, "_redis", lambda: None)
    assert await VC.check_cooldown("a", "u", 60) is True
    await VC.set_cooldown("a", "u", 60)  # no-op
    assert await VC.is_thread_paused("a", "u") is False
    await VC.pause_thread("a", "u")
    await VC.resume_thread("a", "u")

    # exists → cooldown active
    r = _Redis(exists=1)
    monkeypatch.setattr(VC, "_redis", lambda: r)
    assert await VC.check_cooldown("a", "u", 60) is False
    assert r.closed

    # not exists → allowed
    r = _Redis(exists=0)
    monkeypatch.setattr(VC, "_redis", lambda: r)
    assert await VC.check_cooldown("a", "u", 60) is True
    assert await VC.is_thread_paused("a", "u") is False

    # paused key exists
    r = _Redis(exists=1)
    monkeypatch.setattr(VC, "_redis", lambda: r)
    assert await VC.is_thread_paused("a", "u") is True

    # redis error → fail-open
    monkeypatch.setattr(VC, "_redis", lambda: _Redis(raises=True))
    assert await VC.check_cooldown("a", "u", 60) is True
    assert await VC.is_thread_paused("a", "u") is False
    await VC.set_cooldown("a", "u", 60)  # swallows


@pytest.mark.asyncio
async def test_pause_resume_set(monkeypatch):
    r = _Redis()
    monkeypatch.setattr(VC, "_redis", lambda: r)
    await VC.set_cooldown("a", "u", 60)
    assert r.sets == [("viber:cooldown:a:u", "1", 60)]
    await VC.pause_thread("a", "u")
    await VC.resume_thread("a", "u")
    assert r.sets[1] == ("viber:paused:a:u", "1", None)
    assert r.deleted == ["viber:paused:a:u"]
    assert r.closed


def test_redis_factory():
    # redis package installed in container → real client object
    r = VC._redis()
    assert r is not None


def test_redis_factory_import_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "redis.asyncio", None)
    assert VC._redis() is None


def _wire_pipeline(monkeypatch):
    calls = {"mem": []}
    store = AsyncMock(return_value=None)
    store.side_effect = lambda *a: calls["mem"].append(a)
    monkeypatch.setattr(VC, "store_message_memory", store)
    monkeypatch.setattr(VC, "detect_intent",
                        AsyncMock(return_value="question"))
    monkeypatch.setattr(VC, "retrieve_brand_context",
                        AsyncMock(return_value="ctx"))
    reply = AsyncMock(return_value="reply text")
    monkeypatch.setattr(VC, "generate_contextual_reply", reply)
    monkeypatch.setattr(VC, "check_cooldown",
                        AsyncMock(return_value=True))
    monkeypatch.setattr(VC, "is_thread_paused",
                        AsyncMock(return_value=False))
    monkeypatch.setattr(VC, "set_cooldown", AsyncMock())
    return calls, reply


@pytest.mark.asyncio
async def test_process_disabled(monkeypatch, no_redis):
    calls, _ = _wire_pipeline(monkeypatch)
    out = await VC.process_inbound_message(
        "a", "t", "u", "N", "hi", None, {"enabled": False}, "bot")
    assert out["skipped"] and out["reason"] == "auto_reply_disabled"
    assert calls["mem"]  # inbound still stored


@pytest.mark.asyncio
async def test_process_cooldown_and_paused(monkeypatch):
    _wire_pipeline(monkeypatch)
    monkeypatch.setattr(VC, "check_cooldown",
                        AsyncMock(return_value=False))
    out = await VC.process_inbound_message(
        "a", "t", "u", "N", "hi", None, {"enabled": True}, "bot")
    assert out["reason"] == "cooldown_active"

    monkeypatch.setattr(VC, "check_cooldown",
                        AsyncMock(return_value=True))
    monkeypatch.setattr(VC, "is_thread_paused",
                        AsyncMock(return_value=True))
    out = await VC.process_inbound_message(
        "a", "t", "u", "N", "hi", None, {"enabled": True}, "bot")
    assert out["reason"] == "thread_paused"


@pytest.mark.asyncio
async def test_process_spam_and_greeting(monkeypatch):
    from app.services.whatsapp_chatbot import INTENT_GREETING, INTENT_SPAM
    _wire_pipeline(monkeypatch)
    cfg = {"enabled": True}
    monkeypatch.setattr(VC, "detect_intent", AsyncMock(return_value=INTENT_SPAM))
    out = await VC.process_inbound_message(
        "a", "t", "u", "N", "spam", None, cfg, "bot")
    assert out["reason"] == "spam_not_replied"

    monkeypatch.setattr(VC, "detect_intent",
                        AsyncMock(return_value=INTENT_GREETING))
    out = await VC.process_inbound_message(
        "a", "t", "u", "N", "hello", None,
        {"enabled": True, "reply_to_greetings": False}, "bot")
    assert out["reason"] == "greetings_not_replied"

    # spam with reply_to_spam → proceeds
    monkeypatch.setattr(VC, "detect_intent", AsyncMock(return_value=INTENT_SPAM))
    out = await VC.process_inbound_message(
        "a", str(uuid.uuid4()), "u", "N", "spam", None,
        {"enabled": True, "reply_to_spam": True}, "bot")
    assert out["reply"] == "reply text" and not out["skipped"]


@pytest.mark.asyncio
async def test_process_happy(monkeypatch):
    calls, reply = _wire_pipeline(monkeypatch)
    tid = str(uuid.uuid4())
    out = await VC.process_inbound_message(
        "acct", tid, "user1", "N", "pricing?", "tok",
        {"enabled": True, "cooldown_seconds": 30}, "bot")
    assert out["reply"] == "reply text"
    assert out["intent"] == "question"
    # team_uuid parsed + passed
    kw = reply.await_args.kwargs
    assert kw["team_id"] == uuid.UUID(tid)
    assert kw["brand_context"] == "ctx"
    # both memory writes
    assert [m[3] for m in calls["mem"]] == ["them", "me"]

    # invalid team_id → team_uuid None
    out = await VC.process_inbound_message(
        "a", "not-a-uuid", "u", "N", "x", None, {"enabled": True}, "bot")
    assert reply.await_args.kwargs["team_id"] is None
