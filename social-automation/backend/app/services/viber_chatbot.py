"""Viber bot auto-reply pipeline.

Thin channel adapter over the shared chatbot helpers
(:mod:`app.services.whatsapp_chatbot`, :mod:`app.services.messenger_chatbot`)
— same DMR-first inference chain, brand-context RAG, and conversation memory
as the Telegram/WhatsApp bots. Redis keys are namespaced ``viber:`` so state
never collides with the other channels.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any

from app.core.config import get_settings
from app.services.messenger_chatbot import generate_contextual_reply, store_message_memory
from app.services.whatsapp_chatbot import (
    DEFAULT_COOLDOWN_SECONDS,
    INTENT_GREETING,
    INTENT_SPAM,
    detect_intent,
    retrieve_brand_context,
)

logger = logging.getLogger(__name__)
settings = get_settings()

_COOLDOWN_KEY = "viber:cooldown:{account_id}:{user_id}"
_PAUSED_KEY = "viber:paused:{account_id}:{user_id}"


def _redis():
    try:
        import redis.asyncio as aioredis
    except ImportError:
        return None
    url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    return aioredis.from_url(url, decode_responses=True)


async def check_cooldown(account_id: str, user_id: str, cooldown_seconds: int) -> bool:
    r = _redis()
    if r is None:
        return True
    try:
        return not await r.exists(
            _COOLDOWN_KEY.format(account_id=account_id, user_id=user_id)
        )
    except Exception:
        return True
    finally:
        await r.aclose()


async def set_cooldown(account_id: str, user_id: str, cooldown_seconds: int) -> None:
    r = _redis()
    if r is None:
        return
    try:
        await r.set(
            _COOLDOWN_KEY.format(account_id=account_id, user_id=user_id),
            "1",
            ex=max(cooldown_seconds, 1),
        )
    except Exception:
        pass
    finally:
        await r.aclose()


async def is_thread_paused(account_id: str, user_id: str) -> bool:
    r = _redis()
    if r is None:
        return False
    try:
        return bool(
            await r.exists(_PAUSED_KEY.format(account_id=account_id, user_id=user_id))
        )
    except Exception:
        return False
    finally:
        await r.aclose()


async def pause_thread(account_id: str, user_id: str) -> None:
    r = _redis()
    if r is None:
        return
    try:
        await r.set(_PAUSED_KEY.format(account_id=account_id, user_id=user_id), "1")
    finally:
        await r.aclose()


async def resume_thread(account_id: str, user_id: str) -> None:
    r = _redis()
    if r is None:
        return
    try:
        await r.delete(_PAUSED_KEY.format(account_id=account_id, user_id=user_id))
    finally:
        await r.aclose()


async def process_inbound_message(
    account_id: str,
    team_id: str,
    user_id: str,
    sender_name: str,
    message_text: str,
    message_token: str | int | None,
    config: dict,
    account_name: str,
) -> dict[str, Any]:
    """Full inbound pipeline for Viber ``message`` webhook events."""
    _ = sender_name, message_token  # kept for future audit/reply_to support
    result: dict[str, Any] = {
        "reply": None,
        "skipped": False,
        "reason": "",
        "intent": "",
    }

    await store_message_memory(team_id, account_id, str(user_id), "them", message_text)

    if not config.get("enabled", False):
        result["skipped"] = True
        result["reason"] = "auto_reply_disabled"
        return result

    cooldown_seconds = int(
        config.get("cooldown_seconds", DEFAULT_COOLDOWN_SECONDS)
        or DEFAULT_COOLDOWN_SECONDS
    )
    if not await check_cooldown(account_id, user_id, cooldown_seconds):
        result["skipped"] = True
        result["reason"] = "cooldown_active"
        return result

    if await is_thread_paused(account_id, user_id):
        result["skipped"] = True
        result["reason"] = "thread_paused"
        return result

    cf_token = os.getenv("CLOUDFLARE_API_TOKEN", "") or getattr(settings, "CLOUDFLARE_API_TOKEN", "") or ""
    cf_account = os.getenv("CLOUDFLARE_ACCOUNT_ID", "") or getattr(settings, "CLOUDFLARE_ACCOUNT_ID", "") or ""
    dmr_url = os.getenv("DMR_BASE_URL", "") or getattr(settings, "DMR_BASE_URL", "http://host.docker.internal:12435")

    intent = await detect_intent(message_text, cf_token, cf_account, dmr_url)
    result["intent"] = intent

    if intent == INTENT_SPAM and not config.get("reply_to_spam", False):
        result["skipped"] = True
        result["reason"] = "spam_not_replied"
        return result

    if intent == INTENT_GREETING and not config.get("reply_to_greetings", True):
        result["skipped"] = True
        result["reason"] = "greetings_not_replied"
        return result

    brand_context = await retrieve_brand_context(message_text)
    team_uuid: uuid.UUID | None = None
    try:
        team_uuid = uuid.UUID(team_id) if team_id else None
    except (TypeError, ValueError):
        team_uuid = None

    reply_text = await generate_contextual_reply(
        config=config,
        user_message=message_text,
        account_name=account_name,
        account_id=account_id,
        thread_id=str(user_id),
        cf_token=cf_token,
        cf_account=cf_account,
        dmr_url=dmr_url,
        intent=intent,
        brand_context=brand_context,
        team_id=team_uuid,
    )

    await set_cooldown(account_id, user_id, cooldown_seconds)
    await store_message_memory(team_id, account_id, str(user_id), "me", reply_text)
    result["reply"] = reply_text
    return result
