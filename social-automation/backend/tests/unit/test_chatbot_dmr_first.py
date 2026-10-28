"""Chatbot inference ordering tests — DMR primary, Cloudflare failover, static last.

Covers the DMR-first migration for messenger_chatbot.generate_contextual_reply:
  1. DMR up → reply comes from DMR, CF is never called
  2. DMR down → falls back to Cloudflare Workers AI
  3. Both down → static fallback text
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import messenger_chatbot as bot


def _base_patches(dmr_result=None, dmr_exc=None, cf_text=None):
    """Return the context managers patching all external deps of generate_contextual_reply."""
    cf_resp = MagicMock()
    cf_resp.status_code = 200
    cf_resp.json.return_value = {"result": {"response": cf_text or ""}}

    cf_client = MagicMock()
    cf_client.post = AsyncMock(return_value=cf_resp)
    cf_client.__aenter__ = AsyncMock(return_value=cf_client)
    cf_client.__aexit__ = AsyncMock(return_value=False)

    dmr_mock = AsyncMock(
        return_value=dmr_result if dmr_result is not None else {"text": "DMR reply?"}
    )
    if dmr_exc is not None:
        dmr_mock = AsyncMock(side_effect=dmr_exc)

    return (
        patch.object(bot, "get_thread_config", new=AsyncMock(return_value={})),
        patch.object(bot, "_get_brand_voice_block", new=AsyncMock(return_value="")),
        patch.object(bot, "get_conversation_memory", new=AsyncMock(return_value=[])),
        patch.object(bot, "has_disclosed", new=AsyncMock(return_value=True)),
        patch.object(bot, "mark_disclosed", new=AsyncMock()),
        patch.object(bot, "track_bot_reply", new=AsyncMock()),
        patch("app.services.dmr.call_dmr_chat", new=dmr_mock),
        patch.object(bot.httpx, "AsyncClient", return_value=cf_client),
        dmr_mock,
        cf_client,
    )


async def _call(cf_token="tok", cf_account="acct"):
    return await bot.generate_contextual_reply(
        config={},
        user_message="hello, what services do you offer?",
        account_name="Cloudless",
        account_id="acc1",
        thread_id="t1",
        cf_token=cf_token,
        cf_account=cf_account,
        dmr_url="http://dmr:12435",
    )


@pytest.mark.asyncio
async def test_dmr_primary_reply_used_when_dmr_succeeds():
    *ctx, dmr_mock, cf_client = _base_patches(dmr_result={"text": "DMR reply?"})
    with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5], ctx[6], ctx[7]:
        reply = await _call()
    assert reply == "DMR reply?"
    dmr_mock.assert_awaited_once()
    cf_client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_cloudflare_used_when_dmr_fails():
    *ctx, dmr_mock, cf_client = _base_patches(
        dmr_exc=ConnectionError("DMR offline"), cf_text="CF reply?"
    )
    with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5], ctx[6], ctx[7]:
        reply = await _call()
    assert reply == "CF reply?"
    dmr_mock.assert_awaited_once()
    cf_client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_static_fallback_when_both_fail():
    cf_resp = MagicMock()
    cf_resp.status_code = 500
    cf_resp.json.return_value = {}
    cf_client = MagicMock()
    cf_client.post = AsyncMock(return_value=cf_resp)
    cf_client.__aenter__ = AsyncMock(return_value=cf_client)
    cf_client.__aexit__ = AsyncMock(return_value=False)

    dmr_mock = AsyncMock(side_effect=ConnectionError("DMR offline"))
    patches = (
        patch.object(bot, "get_thread_config", new=AsyncMock(return_value={})),
        patch.object(bot, "_get_brand_voice_block", new=AsyncMock(return_value="")),
        patch.object(bot, "get_conversation_memory", new=AsyncMock(return_value=[])),
        patch.object(bot, "has_disclosed", new=AsyncMock(return_value=True)),
        patch.object(bot, "mark_disclosed", new=AsyncMock()),
        patch.object(bot, "track_bot_reply", new=AsyncMock()),
        patch("app.services.dmr.call_dmr_chat", new=dmr_mock),
        patch.object(bot.httpx, "AsyncClient", return_value=cf_client),
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        reply = await bot.generate_contextual_reply(
            config={"fallback_text": "We'll be right back!"},
            user_message="hello?",
            account_name="Cloudless",
            account_id="acc1",
            thread_id="t1",
            cf_token="tok",
            cf_account="acct",
            dmr_url="http://dmr:12435",
        )
    assert reply == "We'll be right back!"
    dmr_mock.assert_awaited_once()
    cf_client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_dmr_gets_chatbot_model_override():
    *ctx, dmr_mock, cf_client = _base_patches(dmr_result={"text": "ok?"})
    with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5], ctx[6], ctx[7]:
        await _call()
    _, kwargs = dmr_mock.call_args
    assert kwargs.get("model_override")  # DMR_CHATBOT_MODEL passed through
    assert "qwen3" in kwargs["model_override"].lower() or "llama" in kwargs["model_override"].lower()
