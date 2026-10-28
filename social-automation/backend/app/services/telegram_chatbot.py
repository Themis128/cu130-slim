"""Telegram Bot chatbot service.

Mirrors WhatsApp auto-reply without Meta's 24-hour window. Threads are keyed
by Telegram ``chat_id``. Reply generation reuses ``messenger_chatbot`` so
pricing guardrails, brand voice, and DMR→CF fallback stay consistent.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import UTC, datetime
from typing import Any

from app.core.config import get_settings
from app.services.messenger_chatbot import generate_contextual_reply, store_message_memory
from app.services.whatsapp_chatbot import (
    INTENT_GREETING,
    INTENT_SPAM,
    detect_intent,
    retrieve_brand_context,
)

logger = logging.getLogger(__name__)
settings = get_settings()

_COOLDOWN_KEY = "telegram:cooldown:{account_id}:{chat_id}"
_PAUSED_KEY = "telegram:paused:{account_id}:{chat_id}"
DEFAULT_COOLDOWN_SECONDS = 300


async def _get_redis() -> Any:
    import redis.asyncio as aioredis

    url = getattr(settings, "MESSENGER_REDIS_URL", None) or settings.REDIS_URL
    return aioredis.from_url(url)


async def check_cooldown(
    account_id: str, chat_id: str, cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS
) -> bool:
    try:
        r = await _get_redis()
        key = _COOLDOWN_KEY.format(account_id=account_id, chat_id=chat_id)
        ttl = await r.ttl(key)
        return ttl <= 0
    except Exception:
        return True


async def set_cooldown(
    account_id: str, chat_id: str, cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS
) -> None:
    try:
        r = await _get_redis()
        key = _COOLDOWN_KEY.format(account_id=account_id, chat_id=chat_id)
        await r.setex(key, cooldown_seconds, "1")
    except Exception:
        pass


async def is_thread_paused(account_id: str, chat_id: str) -> bool:
    try:
        r = await _get_redis()
        key = _PAUSED_KEY.format(account_id=account_id, chat_id=chat_id)
        return bool(await r.exists(key))
    except Exception:
        return False


async def pause_thread(account_id: str, chat_id: str, reason: str = "human_handoff") -> None:
    try:
        r = await _get_redis()
        key = _PAUSED_KEY.format(account_id=account_id, chat_id=chat_id)
        await r.set(
            key,
            json.dumps({"reason": reason, "paused_at": datetime.now(UTC).isoformat()}),
        )
    except Exception:
        pass


async def resume_thread(account_id: str, chat_id: str) -> None:
    try:
        r = await _get_redis()
        key = _PAUSED_KEY.format(account_id=account_id, chat_id=chat_id)
        await r.delete(key)
    except Exception:
        pass


async def index_brand_knowledge(team_id: str, brand_data: dict) -> int:
    """Reuse WhatsApp brand indexing collection for shared RAG."""
    from app.services.whatsapp_chatbot import index_brand_knowledge as _wa_index

    return await _wa_index(team_id, brand_data)


async def process_inbound_message(
    account_id: str,
    team_id: str,
    chat_id: str,
    sender_name: str,
    message_text: str,
    message_id: str | int | None,
    config: dict,
    account_name: str,
) -> dict[str, Any]:
    """Full inbound pipeline for Telegram Bot webhooks."""
    _ = sender_name, message_id  # available for future audit / reply_to
    result: dict[str, Any] = {
        "reply": None,
        "skipped": False,
        "reason": "",
        "intent": "",
    }

    # Always persist inbound text for conversation memory (even if we skip reply).
    await store_message_memory(team_id, account_id, str(chat_id), "them", message_text)

    if not config.get("enabled", False):
        result["skipped"] = True
        result["reason"] = "auto_reply_disabled"
        return result

    cooldown_seconds = int(config.get("cooldown_seconds", DEFAULT_COOLDOWN_SECONDS) or DEFAULT_COOLDOWN_SECONDS)
    if not await check_cooldown(account_id, chat_id, cooldown_seconds):
        result["skipped"] = True
        result["reason"] = "cooldown_active"
        return result

    if await is_thread_paused(account_id, chat_id):
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
        thread_id=str(chat_id),
        cf_token=cf_token,
        cf_account=cf_account,
        dmr_url=dmr_url,
        intent=intent,
        brand_context=brand_context,
        team_id=team_uuid,
    )

    await set_cooldown(account_id, chat_id, cooldown_seconds)
    await store_message_memory(team_id, account_id, str(chat_id), "me", reply_text)
    result["reply"] = reply_text
    return result
