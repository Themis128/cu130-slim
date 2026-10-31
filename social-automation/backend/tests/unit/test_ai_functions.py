"""Coverage for app/services/ai_functions.py — DMR-backed AI functions.

All HTTP is mocked with respx; no real DMR is contacted.
"""

from types import SimpleNamespace

import httpx
import pytest
import respx

import app.services.ai_functions as F

CHAT = "http://dmr.test/v1/chat/completions"
EMBED = "http://dmr.test/v1/embeddings"


@pytest.fixture
def cfg(monkeypatch):
    monkeypatch.setattr(F, "get_settings", lambda: SimpleNamespace(
        DMR_BASE_URL=CHAT, DMR_EMBED_URL=EMBED,
        DMR_MODEL="m", DMR_EMBED_MODEL="e"))


def _chat_reply(text: str):
    return httpx.Response(200, json={
        "choices": [{"message": {"content": text}}]})


def _embed_reply(vec=None):
    return httpx.Response(200, json={
        "data": [{"embedding": vec or [1.0, 0.0]}]})


@pytest.mark.asyncio
class TestChatFns:
    async def test_fix_grammar_returns_cleaned(self, cfg, respx_mock):
        respx_mock.post(CHAT).mock(return_value=_chat_reply("Fixed text."))
        assert await F.fix_grammar("fxed txt") == "Fixed text."

    async def test_fix_grammar_passthrough_on_error(self, cfg, respx_mock):
        respx_mock.post(CHAT).mock(side_effect=httpx.ConnectError("down"))
        assert await F.fix_grammar("orig") == "orig"

    async def test_classify_picks_label(self, cfg, respx_mock):
        respx_mock.post(CHAT).mock(return_value=_chat_reply("buying-interest"))
        out = await F.ai_classify(["buying-interest", "casual"], "can i get a quote?")
        assert out == "buying-interest"

    async def test_classify_falls_back_on_bad_label(self, cfg, respx_mock):
        respx_mock.post(CHAT).mock(return_value=_chat_reply("garbage"))
        assert await F.ai_classify(["a", "b"], "t") == "a"

    async def test_extract_returns_value(self, cfg, respx_mock):
        respx_mock.post(CHAT).mock(return_value=_chat_reply("42"))
        assert await F.ai_extract("score", "t") == "42"

    async def test_mask_redacts(self, cfg, respx_mock):
        respx_mock.post(CHAT).mock(return_value=_chat_reply("contact [REDACTED-email]"))
        assert "[REDACTED-email]" in await F.ai_mask("contact a@b.c")


@pytest.mark.asyncio
class TestSimilarity:
    async def test_identical_is_one(self, cfg, respx_mock):
        respx_mock.post(EMBED).mock(return_value=_embed_reply([1.0, 0.0]))
        assert await F.ai_similarity("a", "a") == pytest.approx(1.0)

    async def test_error_returns_zero(self, cfg, respx_mock):
        respx_mock.post(EMBED).mock(side_effect=httpx.ConnectError("down"))
        assert await F.ai_similarity("a", "b") == 0.0


@pytest.mark.asyncio
class TestMediaGate:
    async def test_passes_good_post(self, cfg, respx_mock):
        respx_mock.post(CHAT).mock(side_effect=[
            _chat_reply("clean post"), _chat_reply("90")])
        out = await F.media_gate("post")
        assert out["ok"] is True and out["score"] == 90

    async def test_fails_low_nlp(self, cfg, respx_mock):
        respx_mock.post(CHAT).mock(side_effect=[
            _chat_reply("clean"), _chat_reply("50")])
        out = await F.media_gate("post")
        assert out["ok"] is False and "nlp" in out["reason"]

    async def test_fails_semantic_dup(self, cfg, respx_mock):
        respx_mock.post(CHAT).mock(side_effect=[
            _chat_reply("clean"), _chat_reply("90")])
        respx_mock.post(EMBED).mock(return_value=_embed_reply([1.0, 0.0]))
        out = await F.media_gate("post", recent_texts=["same post"])
        assert out["ok"] is False and "sim" in out["reason"]


@pytest.mark.asyncio
class TestGates:
    async def test_semantic_gate_blocks_dup(self, cfg, respx_mock):
        from app.services.content_sanitizer import content_semantic_gate
        respx_mock.post(EMBED).mock(return_value=_embed_reply([1.0, 0.0]))
        ok, reason = await content_semantic_gate("new", ["old"])
        assert ok is False and "semantic" in reason

    async def test_classify_helper_degrades(self, cfg, respx_mock):
        from app.services.lead_capture import classify_inbound_intent
        respx_mock.post(CHAT).mock(return_value=_chat_reply("support"))
        assert await classify_inbound_intent("help me") == "support"
