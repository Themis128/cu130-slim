"""Unit tests for app/services/messenger_chatbot.py — Messenger/IG bot brain.

Covers track_bot_reply telemetry, Redis state (cooldown/pause/disclosure/
IG app-level rate limit), frustration detection, pricing guardrail,
Greek detection + steering-question appender, brand-voice block fetch/
cache, and the DMR→CF→static reply chain.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, Mock

import pytest

import app.services.messenger_chatbot as M


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
    monkeypatch.setattr(M, "_get_redis", AsyncMock(return_value=r))
    return r


# ── pure helpers ──────────────────────────────────────────────────────


def test_is_valid_uuid():
    assert M._is_valid_uuid(str(uuid.uuid4())) is True
    assert M._is_valid_uuid("not-a-uuid") is False


def test_is_frustrated_message():
    assert M.is_frustrated_message("stop messaging me") is True
    assert M.is_frustrated_message("σταμάτα με ενοχλείς") is True
    assert M.is_frustrated_message("talk to a human please") is True
    assert M.is_frustrated_message("thanks, this is great") is False
    assert M.is_frustrated_message("") is False
    assert M.is_frustrated_message(None) is False


def test_pricing_and_greek_detectors():
    assert M._is_pricing_question("how much does it cost?") is True
    assert M._is_pricing_question("πόσο κοστίζει;") is True
    assert M._is_pricing_question("what services do you offer") is False
    assert M._is_greek_message("γεια σου") is True
    assert M._is_greek_message("hello") is False


def test_ensure_steering_question():
    assert M._ensure_steering_question("what?", False) == "what?"
    out = M._ensure_steering_question("we can help.", False)
    assert out.endswith("?") and out.startswith("we can help.")
    out_gr = M._ensure_steering_question("μπορούμε να βοηθήσουμε.", True)
    assert out_gr != "μπορούμε να βοηθήσουμε."
    assert M._ensure_steering_question("", False) == ""


# ── track_bot_reply ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_track_bot_reply(monkeypatch):
    import app.services.usage_tracker as U

    track = AsyncMock()
    monkeypatch.setattr(U, "track_inference", track)
    tid = uuid.uuid4()
    await M.track_bot_reply("not-uuid", "t1", provider="dmr", model="m", user_message="hi", reply_text="yo", latency_ms=10, intent="greeting", team_id=tid)
    kw = track.await_args.kwargs
    assert kw["endpoint"] == "bot_reply"
    assert kw["meta_data"]["guardrail"] == ""

    # no team_id → analytics event path skipped, no error
    await M.track_bot_reply("a", "t", provider="cf", model="m", user_message="u", reply_text="r")
    # tracker failure is swallowed
    track.side_effect = RuntimeError("boom")
    await M.track_bot_reply("a", "t", provider="cf", model="m", user_message="u", reply_text="r", team_id=tid)


# ── Redis state helpers ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_messenger_redis_helpers(redis):
    assert await M.check_cooldown("a", "t") is True
    await M.set_cooldown("a", "t", 60)
    assert await M.check_cooldown("a", "t") is False

    assert await M.is_thread_paused("a", "t") is False
    await M.pause_thread("a", "t")
    assert await M.is_thread_paused("a", "t") is True
    await M.resume_thread("a", "t")
    assert await M.is_thread_paused("a", "t") is False

    assert await M.has_disclosed("a", "t") is False
    await M.mark_disclosed("a", "t")
    assert await M.has_disclosed("a", "t") is True

    assert await M.get_thread_config("a", "t") == {}
    await M.set_thread_config("a", "t", {"x": 1})
    assert await M.get_thread_config("a", "t") == {"x": 1}


@pytest.mark.asyncio
async def test_ig_app_rate_limit(redis):
    assert await M.is_instagram_app_rate_limited() is False
    await M.set_instagram_app_rate_limited(60)
    assert await M.is_instagram_app_rate_limited() is True


@pytest.mark.asyncio
async def test_redis_down_defaults(monkeypatch):
    monkeypatch.setattr(M, "_get_redis", AsyncMock(side_effect=RuntimeError("down")))
    assert await M.check_cooldown("a", "t") is True
    assert await M.is_thread_paused("a", "t") is False
    assert await M.is_instagram_app_rate_limited() is False
    assert await M.has_disclosed("a", "t") is False
    await M.set_cooldown("a", "t")
    await M.mark_disclosed("a", "t")
    await M.set_instagram_app_rate_limited(10)
    assert await M.get_thread_config("a", "t") == {}


# ── brand voice block ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_brand_voice_block(monkeypatch):
    M._brand_voice_cache["block"] = ""
    M._brand_voice_cache["ts"] = 0

    class _Client:
        def __init__(self):
            self.posts = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            r = Mock()
            r.status_code = 200
            r.json = lambda: {"access_token": "tok"}
            return r

        async def get(self, url, **kw):
            r = Mock()
            r.status_code = 200
            r.json = lambda: {
                "banned_phrases": ["cheap af"],
                "preferred_phrases": ["clear skies"],
                "voice_signature": {"pricing_currency": "EUR", "language": "el+en", "disclosure": True},
            }
            return r

    monkeypatch.setenv("SOCIAL_ADMIN_EMAIL", "a@x.io")
    monkeypatch.setenv("SOCIAL_ADMIN_PASSWORD", "p")
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda **kw: _Client())
    block = await M._get_brand_voice_block()
    assert "BRAND VOICE RULES" in block
    assert "clear skies" in block and "cheap af" in block
    assert "euros" in block and "match the user" in block.lower()
    assert "Always say you are a bot" in block
    # cached — second call returns without HTTP
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda **kw: (_ for _ in ()).throw(AssertionError("refetch")))
    assert await M._get_brand_voice_block() == block
    M._brand_voice_cache["block"] = ""
    M._brand_voice_cache["ts"] = 0


@pytest.mark.asyncio
async def test_brand_voice_block_failures(monkeypatch):
    M._brand_voice_cache["block"] = ""
    M._brand_voice_cache["ts"] = 0

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            raise RuntimeError("auth down")

    monkeypatch.delenv("SOCIAL_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("SOCIAL_ADMIN_PASSWORD", raising=False)
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda **kw: _Client())
    # auth raises inside try → whole thing degrades to ""
    assert await M._get_brand_voice_block() == ""


# ── detect_intent / memory / RAG (same seam patterns as whatsapp) ────


@pytest.mark.asyncio
async def test_detect_intent_keyword_path(monkeypatch):
    import app.services.dmr as D

    monkeypatch.setattr(D, "call_dmr_chat", AsyncMock(side_effect=RuntimeError("x")))
    assert await M.detect_intent("what is the price", "", "", "u") == "business"
    assert await M.detect_intent("καλησπέρα", "", "", "u") == "greeting"


@pytest.mark.asyncio
async def test_memory_and_brand_degrade(monkeypatch):
    import app.services.chroma_client as C

    monkeypatch.setattr(C, "_get_embedding", AsyncMock(return_value=None))
    monkeypatch.setattr(C, "_get_collection_id", AsyncMock(return_value=None))
    monkeypatch.setattr(C, "_collection_base_url", lambda: "http://c")
    posts = []

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            posts.append(url)
            r = Mock()
            r.status_code = 200
            r.json = lambda: {}
            return r

    monkeypatch.setattr(M.httpx, "AsyncClient", lambda **kw: _Client())
    await M.store_message_memory("t", "a", "th", "them", "hi")
    assert await M.get_conversation_memory("a", "th") == []
    assert await M.index_brand_knowledge("t", {"name": "x"}) == 0
    assert await M.retrieve_brand_context("q") == ""


# ── generate_contextual_reply chain ──────────────────────────────────


def _kw(**kw):
    base = dict(
        config={"enabled": True, "fallback_text": "back soon"},
        user_message="hello",
        account_name="Cloudless",
        account_id="a1",
        thread_id="t1",
        cf_token="",
        cf_account="",
        dmr_url="http://dmr",
    )
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_reply_pricing_guardrail(monkeypatch, redis):
    track = AsyncMock()
    monkeypatch.setattr(M, "track_bot_reply", track)
    out = await M.generate_contextual_reply(**_kw(user_message="πόσο κοστίζει;"))
    assert out.startswith(M._PRICING_RESPONSES["greek"])
    assert await M.has_disclosed("a1", "t1")
    assert track.await_args.kwargs["guardrail_triggered"] == "pricing"


@pytest.mark.asyncio
async def test_reply_dmr_chain(monkeypatch, redis):
    import app.services.dmr as D

    dmr = AsyncMock(return_value={"text": "Here is the info."})
    monkeypatch.setattr(D, "call_dmr_chat", dmr)
    monkeypatch.setattr(M, "track_bot_reply", AsyncMock())
    monkeypatch.setattr(M, "get_conversation_memory", AsyncMock(return_value=[]))
    monkeypatch.setattr(M, "_get_brand_voice_block", AsyncMock(return_value="VOICE"))
    out = await M.generate_contextual_reply(**_kw(intent="business", brand_context="- brand"))
    # DMR text gets disclosure prefix + steering question appended
    assert out.startswith("🤖 Auto-reply: Here is the info.")
    assert out.rstrip().endswith("?")
    assert dmr.await_args.kwargs["system"].find("VOICE") >= 0


@pytest.mark.asyncio
async def test_reply_cf_then_static(monkeypatch, redis):
    import app.services.dmr as D

    monkeypatch.setattr(D, "call_dmr_chat", AsyncMock(side_effect=RuntimeError("dead")))
    monkeypatch.setattr(M, "track_bot_reply", AsyncMock())
    monkeypatch.setattr(M, "get_conversation_memory", AsyncMock(return_value=[{"sender": "them", "text": "earlier"}]))
    monkeypatch.setattr(M, "_get_brand_voice_block", AsyncMock(return_value=""))

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            r = Mock()
            r.status_code = 200
            r.json = lambda: {"result": {"response": "cf says hi"}}
            return r

    monkeypatch.setattr(M.httpx, "AsyncClient", lambda **kw: _Client())
    out = await M.generate_contextual_reply(**_kw(cf_token="t", cf_account="a"))
    assert out.startswith("🤖 Auto-reply: cf says hi")

    # all providers dead → static fallback (no disclosure 2nd time)
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda **kw: _Client.__new__(_Client))
    await M.mark_disclosed("a1", "t1")
    out2 = await M.generate_contextual_reply(**_kw(cf_token="", cf_account=""))
    assert out2 == "back soon"
