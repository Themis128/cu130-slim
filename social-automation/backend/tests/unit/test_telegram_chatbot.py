"""Unit tests for app/services/telegram_chatbot.py.

Redis is faked at the module boundary; intent detection, brand context,
and reply generation are monkeypatched — the orchestration logic is real.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import app.services.telegram_chatbot as tc


class _FakeRedis:
    def __init__(self):
        self.store = {}

    async def ttl(self, key):
        return self.store.get(key, -2)

    async def setex(self, key, ttl, val):
        self.store[key] = ttl

    async def exists(self, key):
        return 1 if key in self.store else 0

    async def set(self, key, val):
        self.store[key] = val

    async def delete(self, key):
        self.store.pop(key, None)


@pytest.fixture
def fake_redis(monkeypatch):
    r = _FakeRedis()
    monkeypatch.setattr(tc, "_get_redis", AsyncMock(return_value=r))
    return r


# ── cooldown / pause primitives ─────────────────────────────────────


@pytest.mark.asyncio
async def test_cooldown_roundtrip(fake_redis):
    assert await tc.check_cooldown("a", "c") is True   # no key → free
    await tc.set_cooldown("a", "c", 60)
    assert await tc.check_cooldown("a", "c") is False  # key present
    await tc.set_cooldown("a", "c", 0)
    assert await tc.check_cooldown("a", "c") is True   # ttl<=0 → free


@pytest.mark.asyncio
async def test_cooldown_redis_failure_failopen(monkeypatch):
    monkeypatch.setattr(tc, "_get_redis", AsyncMock(side_effect=RuntimeError("down")))
    assert await tc.check_cooldown("a", "c") is True
    await tc.set_cooldown("a", "c")  # must not raise


@pytest.mark.asyncio
async def test_pause_resume(fake_redis):
    assert await tc.is_thread_paused("a", "c") is False
    await tc.pause_thread("a", "c", reason="human_handoff")
    assert await tc.is_thread_paused("a", "c") is True
    import json
    meta = json.loads(fake_redis.store["telegram:paused:a:c"])
    assert meta["reason"] == "human_handoff"
    await tc.resume_thread("a", "c")
    assert await tc.is_thread_paused("a", "c") is False


@pytest.mark.asyncio
async def test_pause_redis_failure_quiet(monkeypatch):
    monkeypatch.setattr(tc, "_get_redis", AsyncMock(side_effect=RuntimeError("down")))
    await tc.pause_thread("a", "c")
    await tc.resume_thread("a", "c")
    assert await tc.is_thread_paused("a", "c") is False


@pytest.mark.asyncio
async def test_index_brand_knowledge_delegates(monkeypatch):
    called = {}

    async def _idx(team_id, brand_data):
        called["team"] = team_id
        return 7

    import app.services.whatsapp_chatbot as wc
    monkeypatch.setattr(wc, "index_brand_knowledge", _idx)
    assert await tc.index_brand_knowledge("t1", {"x": 1}) == 7
    assert called["team"] == "t1"


# ── process_inbound_message ─────────────────────────────────────────


def _cfg(**kw):
    base = {"enabled": True, "cooldown_seconds": 60}
    base.update(kw)
    return base


@pytest.fixture
def pipeline(monkeypatch, fake_redis):
    monkeypatch.setattr(tc, "store_message_memory", AsyncMock())
    monkeypatch.setattr(tc, "detect_intent", AsyncMock(return_value="question"))
    monkeypatch.setattr(tc, "retrieve_brand_context", AsyncMock(return_value="ctx"))
    monkeypatch.setattr(tc, "generate_contextual_reply", AsyncMock(return_value="reply text"))
    return tc


@pytest.mark.asyncio
async def test_pipeline_disabled(pipeline):
    out = await tc.process_inbound_message(
        "a", "t", "c", "S", "hi", 1, _cfg(enabled=False), "acc")
    assert out["skipped"] and out["reason"] == "auto_reply_disabled"


@pytest.mark.asyncio
async def test_pipeline_happy(pipeline):
    out = await tc.process_inbound_message(
        "a", "t", "c", "S", "hi", 1, _cfg(), "acc")
    assert out["reply"] == "reply text"
    assert out["intent"] == "question"
    assert tc.store_message_memory.await_count == 2  # them + me
    tc.generate_contextual_reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_pipeline_cooldown(pipeline):
    await tc.set_cooldown("a", "c", 60)
    out = await tc.process_inbound_message(
        "a", "t", "c", "S", "hi", 1, _cfg(), "acc")
    assert out["skipped"] and out["reason"] == "cooldown_active"


@pytest.mark.asyncio
async def test_pipeline_paused(pipeline):
    await tc.pause_thread("a", "c")
    out = await tc.process_inbound_message(
        "a", "t", "c", "S", "hi", 1, _cfg(), "acc")
    assert out["skipped"] and out["reason"] == "thread_paused"


@pytest.mark.asyncio
async def test_pipeline_spam_and_greeting_gates(pipeline):
    tc.detect_intent.return_value = tc.INTENT_SPAM
    out = await tc.process_inbound_message(
        "a", "t", "c", "S", "buy now", 1, _cfg(), "acc")
    assert out["skipped"] and out["reason"] == "spam_not_replied"
    # reply_to_spam=True bypasses the gate
    out = await tc.process_inbound_message(
        "a", "t", "c2", "S", "buy now", 1, _cfg(reply_to_spam=True), "acc")
    assert out["reply"] == "reply text"

    tc.detect_intent.return_value = tc.INTENT_GREETING
    out = await tc.process_inbound_message(
        "a", "t", "c3", "S", "hello", 1, _cfg(reply_to_greetings=False), "acc")
    assert out["skipped"] and out["reason"] == "greetings_not_replied"
    out = await tc.process_inbound_message(
        "a", "t", "c4", "S", "hello", 1, _cfg(), "acc")
    assert out["reply"] == "reply text"


@pytest.mark.asyncio
async def test_pipeline_bad_team_id(pipeline):
    # non-UUID team_id → team_uuid None, reply still generated
    out = await tc.process_inbound_message(
        "a", "not-a-uuid", "c", "S", "hi", 1, _cfg(), "acc")
    assert out["reply"] == "reply text"
    kw = tc.generate_contextual_reply.await_args.kwargs
    assert kw["team_id"] is None
