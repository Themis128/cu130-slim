"""Unit tests for app/services/whatsapp_chatbot.py — WhatsApp bot brain.

Covers the Redis state helpers (cooldown, human-handoff pause, 24h
service window, disclosure, per-thread config), ChromaDB conversation
memory + brand RAG, intent detection (DMR→CF→keywords), the pricing
guardrail, contextual reply generation (DMR→CF→static + Meta disclosure),
and the full process_inbound_message pipeline.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, Mock

import pytest

import app.services.whatsapp_chatbot as W


class _Redis:
    def __init__(self):
        self.kv = {}
        self.ttls = {}

    async def ttl(self, k):
        return self.ttls.get(k, -2)

    async def setex(self, k, t, v):
        self.kv[k] = v
        self.ttls[k] = t

    async def set(self, k, v):
        self.kv[k] = v

    async def get(self, k):
        return self.kv.get(k)

    async def exists(self, k):
        return 1 if k in self.kv else 0

    async def delete(self, k):
        self.kv.pop(k, None)
        self.ttls.pop(k, None)


@pytest.fixture
def redis(monkeypatch):
    r = _Redis()
    monkeypatch.setattr(W, "_get_redis", AsyncMock(return_value=r))
    return r


# ── Redis state helpers ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cooldown(redis):
    assert await W.check_cooldown("a", "p") is True
    await W.set_cooldown("a", "p", 60)
    assert await W.check_cooldown("a", "p") is False


@pytest.mark.asyncio
async def test_pause_resume(redis):
    assert await W.is_thread_paused("a", "p") is False
    await W.pause_thread("a", "p", reason="handoff")
    assert await W.is_thread_paused("a", "p") is True
    await W.resume_thread("a", "p")
    assert await W.is_thread_paused("a", "p") is False


@pytest.mark.asyncio
async def test_service_window(redis):
    assert await W.is_in_service_window("a", "p") is False
    assert await W.get_window_remaining("a", "p") == -2
    await W.refresh_service_window("a", "p")
    assert await W.is_in_service_window("a", "p") is True
    assert await W.get_window_remaining("a", "p") == 86400


@pytest.mark.asyncio
async def test_disclosure(redis):
    assert await W.has_disclosed("a", "p") is False
    await W.mark_disclosed("a", "p")
    assert await W.has_disclosed("a", "p") is True


@pytest.mark.asyncio
async def test_thread_config(redis):
    assert await W.get_thread_config("a", "p") == {}
    await W.set_thread_config("a", "p", {"system_prompt": "be terse"})
    assert await W.get_thread_config("a", "p") == {"system_prompt": "be terse"}


@pytest.mark.asyncio
async def test_redis_down_defaults(monkeypatch):
    # Redis down → permissive defaults (reply allowed, not paused, window open)
    monkeypatch.setattr(W, "_get_redis", AsyncMock(side_effect=RuntimeError("down")))
    assert await W.check_cooldown("a", "p") is True
    assert await W.is_thread_paused("a", "p") is False
    assert await W.is_in_service_window("a", "p") is True
    assert await W.get_window_remaining("a", "p") == 0
    assert await W.has_disclosed("a", "p") is False
    assert await W.get_thread_config("a", "p") == {}
    await W.set_cooldown("a", "p")  # no raise
    await W.pause_thread("a", "p")
    await W.mark_disclosed("a", "p")
    await W.set_thread_config("a", "p", {})


# ── ChromaDB memory / brand RAG ───────────────────────────────────────


def _patch_chroma(monkeypatch, *, col_id="col-1", embedding=(0.1, 0.2), post_resp=None):
    import app.services.chroma_client as C

    monkeypatch.setattr(C, "_get_embedding", AsyncMock(return_value=embedding))
    monkeypatch.setattr(C, "_get_collection_id", AsyncMock(return_value=col_id))
    monkeypatch.setattr(C, "_collection_base_url", lambda: "http://chroma/c")
    posts = []

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            posts.append((url, kw.get("json")))
            r = Mock()
            r.status_code = 200
            r.json = lambda: post_resp or {}
            return r

    monkeypatch.setattr(W.httpx, "AsyncClient", lambda **kw: _Client())
    return posts


@pytest.mark.asyncio
async def test_store_and_get_memory(monkeypatch):
    posts = _patch_chroma(monkeypatch, post_resp={"documents": ["hi", "hello"], "metadatas": [{"sender": "them"}, {"sender": "me"}]})
    await W.store_message_memory("t", "acct", "+30 699", "them", "hi")
    assert posts[0][1]["documents"] == ["hi"]
    assert posts[0][1]["metadatas"][0]["sender"] == "them"

    mem = await W.get_conversation_memory("acct", "+30 699")
    assert mem == [{"sender": "them", "text": "hi"}, {"sender": "me", "text": "hello"}]


@pytest.mark.asyncio
async def test_memory_degrades_when_chroma_off(monkeypatch):
    _patch_chroma(monkeypatch, col_id=None)
    await W.store_message_memory("t", "a", "p", "them", "x")  # no raise
    assert await W.get_conversation_memory("a", "p") == []
    # no embedding → store short-circuits
    _patch_chroma(monkeypatch, embedding=None)
    await W.store_message_memory("t", "a", "p", "them", "x")


@pytest.mark.asyncio
async def test_brand_knowledge_index_and_retrieve(monkeypatch):
    posts = _patch_chroma(monkeypatch, post_resp={"documents": [["Brand name: Cloudless", "Tagline: x"]]})
    n = await W.index_brand_knowledge(
        "team1",
        {
            "name": "Cloudless",
            "tagline": "Clear skies",
            "positioning_statement": "pos",
            "mission": "m",
            "industry": "automation",
            "values": ["a", "b"],
            "target_audience": {"size": "smb", "profile": "founders"},
            "competitor_names": ["zapier"],
        },
    )
    assert n == 8  # one doc per populated field
    metas = [p[1]["metadatas"][0] for p in posts]
    assert all(m["team_id"] == "team1" for m in metas)

    out = await W.retrieve_brand_context("pricing?")
    assert "- Brand name: Cloudless" in out

    # empty embedding → ""
    _patch_chroma(monkeypatch, embedding=None)
    assert await W.retrieve_brand_context("q") == ""


# ── intent detection ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_detect_intent_dmr(monkeypatch):
    import app.services.dmr as D

    monkeypatch.setattr(D, "call_dmr_chat", AsyncMock(return_value={"text": "business"}))
    assert await W.detect_intent("how much?", "tok", "acct", "u") == "business"


@pytest.mark.asyncio
async def test_detect_intent_cf_fallback(monkeypatch):
    import app.services.dmr as D

    monkeypatch.setattr(D, "call_dmr_chat", AsyncMock(side_effect=RuntimeError("dmr down")))

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            r = Mock()
            r.status_code = 200
            r.json = lambda: {"result": {"response": "greeting"}}
            return r

    monkeypatch.setattr(W.httpx, "AsyncClient", lambda **kw: _Client())
    out = await W.detect_intent("hi there", "tok", "acct", "u")
    assert out == "greeting"


@pytest.mark.asyncio
async def test_detect_intent_keywords(monkeypatch):
    import app.services.dmr as D

    monkeypatch.setattr(D, "call_dmr_chat", AsyncMock(side_effect=RuntimeError("x")))
    # no cf creds → keyword fallbacks
    assert await W.detect_intent("what is the price?", "", "", "u") == "business"
    assert await W.detect_intent("καλημέρα", "", "", "u") == "greeting"
    assert await W.detect_intent("what time is it?", "", "", "u") == "question"
    assert await W.detect_intent("win free stuff click here", "", "", "u") == "spam"
    assert await W.detect_intent("just thinking about you", "", "", "u") == "personal"


# ── generate_contextual_reply ─────────────────────────────────────────


def _reply_kwargs(**kw):
    base = dict(
        config={"enabled": True},
        user_message="hello",
        account_name="Cloudless",
        account_id="a1",
        phone="30699",
        cf_token="tok",
        cf_account="acct",
        dmr_url="http://dmr",
    )
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_pricing_guardrail(monkeypatch, redis):
    monkeypatch.setattr(W, "_is_pricing_question", lambda m: True)
    track = AsyncMock()
    monkeypatch.setattr(W, "track_bot_reply", track)
    reply = await W.generate_contextual_reply(**_reply_kwargs(user_message="what's the price?"))
    # deterministic pricing response — never reaches the LLM
    assert reply == W._PRICING_RESPONSES["english"]
    assert await W.has_disclosed("a1", "30699")
    assert track.await_args.kwargs["guardrail_triggered"] == "pricing"


@pytest.mark.asyncio
async def test_reply_dmr_first_contact_disclosure(monkeypatch, redis):
    monkeypatch.setattr(W, "_is_pricing_question", lambda m: False)
    import app.services.dmr as D

    monkeypatch.setattr(D, "call_dmr_chat", AsyncMock(return_value={"text": "Sure, happy to help."}))
    monkeypatch.setattr(W, "track_bot_reply", AsyncMock())
    monkeypatch.setattr(W, "get_conversation_memory", AsyncMock(return_value=[]))
    import app.services.messenger_chatbot as M

    monkeypatch.setattr(M, "_get_brand_voice_block", AsyncMock(return_value=""))
    reply = await W.generate_contextual_reply(**_reply_kwargs(intent="greeting", brand_context="- brand"))
    assert reply == "🤖 Sure, happy to help."  # disclosure prefix
    assert await W.has_disclosed("a1", "30699")

    # second contact → no prefix
    monkeypatch.setattr(D, "call_dmr_chat", AsyncMock(return_value={"text": "again"}))
    reply2 = await W.generate_contextual_reply(**_reply_kwargs())
    assert reply2 == "again"


@pytest.mark.asyncio
async def test_reply_cf_fallback(monkeypatch, redis):
    monkeypatch.setattr(W, "_is_pricing_question", lambda m: False)
    import app.services.dmr as D

    monkeypatch.setattr(D, "call_dmr_chat", AsyncMock(side_effect=RuntimeError("dmr dead")))
    monkeypatch.setattr(W, "track_bot_reply", AsyncMock())
    monkeypatch.setattr(W, "get_conversation_memory", AsyncMock(return_value=[{"sender": "them", "text": "earlier"}]))
    import app.services.messenger_chatbot as M

    monkeypatch.setattr(M, "_get_brand_voice_block", AsyncMock(return_value="VOICE"))

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            r = Mock()
            r.status_code = 200
            r.json = lambda: {"result": {"response": "cf reply"}}
            return r

    monkeypatch.setattr(W.httpx, "AsyncClient", lambda **kw: _Client())
    reply = await W.generate_contextual_reply(**_reply_kwargs())
    assert reply == "🤖 cf reply"
    # memory + brand voice were injected into the prompt — just verify path
    assert await W.has_disclosed("a1", "30699")


@pytest.mark.asyncio
async def test_reply_static_fallback(monkeypatch, redis):
    monkeypatch.setattr(W, "_is_pricing_question", lambda m: False)
    import app.services.dmr as D

    monkeypatch.setattr(D, "call_dmr_chat", AsyncMock(side_effect=RuntimeError("dead")))
    monkeypatch.setattr(W, "track_bot_reply", AsyncMock())
    monkeypatch.setattr(W, "get_conversation_memory", AsyncMock(return_value=[]))
    import app.services.messenger_chatbot as M

    monkeypatch.setattr(M, "_get_brand_voice_block", AsyncMock(return_value=""))
    # no cf creds → static fallback
    reply = await W.generate_contextual_reply(**_reply_kwargs(cf_token="", cf_account="", config={"fallback_text": "BRB!"}))
    assert reply == "🤖 BRB!"


# ── process_inbound_message pipeline ─────────────────────────────────


def _pipeline_patches(monkeypatch, **kw):
    monkeypatch.setattr(W, "refresh_service_window", AsyncMock())
    monkeypatch.setattr(W, "store_message_memory", AsyncMock())
    monkeypatch.setattr(W, "retrieve_brand_context", AsyncMock(return_value=""))
    monkeypatch.setattr(W, "set_cooldown", AsyncMock())
    monkeypatch.setattr(W, "check_cooldown", AsyncMock(return_value=kw.get("cooldown_ok", True)))
    monkeypatch.setattr(W, "is_thread_paused", AsyncMock(return_value=kw.get("paused", False)))
    monkeypatch.setattr(W, "detect_intent", AsyncMock(return_value=kw.get("intent", "question")))
    monkeypatch.setattr(W, "generate_contextual_reply", AsyncMock(return_value=kw.get("reply", "bot reply")))


@pytest.mark.asyncio
async def test_process_inbound_skips(monkeypatch, redis):
    _pipeline_patches(monkeypatch)
    base = dict(account_id="a", team_id=str(uuid.uuid4()), phone="p", sender_name="S", message_text="hi", message_id="m1", account_name="Cloudless")

    out = await W.process_inbound_message(**base, config={"enabled": False})
    assert out["reason"] == "auto_reply_disabled"

    _pipeline_patches(monkeypatch, cooldown_ok=False)
    out = await W.process_inbound_message(**base, config={"enabled": True})
    assert out["reason"] == "cooldown_active"

    _pipeline_patches(monkeypatch, paused=True)
    out = await W.process_inbound_message(**base, config={"enabled": True})
    assert out["reason"] == "thread_paused"

    _pipeline_patches(monkeypatch, intent="spam")
    out = await W.process_inbound_message(**base, config={"enabled": True, "reply_to_spam": False})
    assert out["reason"] == "spam_not_replied"

    _pipeline_patches(monkeypatch, intent="greeting")
    out = await W.process_inbound_message(**base, config={"enabled": True, "reply_to_greetings": False})
    assert out["reason"] == "greetings_not_replied"


@pytest.mark.asyncio
async def test_process_inbound_full_path(monkeypatch, redis):
    _pipeline_patches(monkeypatch, intent="question", reply="the answer")
    out = await W.process_inbound_message(
        account_id="a",
        team_id=str(uuid.uuid4()),
        phone="p",
        sender_name="S",
        message_text="how?",
        message_id="m1",
        config={"enabled": True},
        account_name="Cloudless",
    )
    assert out["reply"] == "the answer"
    assert out["intent"] == "question"
    assert out["skipped"] is False
