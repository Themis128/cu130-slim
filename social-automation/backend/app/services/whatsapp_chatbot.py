"""WhatsApp Business chatbot service.

Provides conversation memory, brand knowledge RAG, intent detection,
per-conversation configuration, human handoff, 24-hour window awareness,
and rate-limit-aware cooldowns for the WhatsApp auto-reply system.

Mirrors app/services/messenger_chatbot.py with WhatsApp-specific adaptations:
- Threads are keyed by phone number (not PSID)
- 24-hour customer service window enforcement (Meta policy)
- Bot disclosure per Meta WhatsApp Business Messaging Policy
- Unified AI routing (Cloudflare Workers AI first, DMR fallback)
- Brand voice injection from Brand system API (banned phrases, euro pricing,
  bilingual Greek/English matching, bot disclosure)

Services used (free/open-source-first):
    - Cloudflare Workers AI (Llama 3.1 8B) — primary reply generation (Greek + English)
    - Docker Model Runner (Qwen3 8B, local) — fallback reply generation + intent detection
    - ChromaDB (local) / Cloudflare Vectorize (cloud) — conversation memory + brand RAG
    - Redis — per-conversation cooldown + paused-thread tracking + 24h window
    - Brand Voice API — banned phrases, preferred phrases, euro pricing, bilingual rules
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import get_settings
from app.services.messenger_chatbot import (
    _PRICING_RESPONSES,
    _is_greek_message,
    _is_pricing_question,
    track_bot_reply,
)

logger = logging.getLogger(__name__)

settings = get_settings()

# Intent categories
INTENT_BUSINESS = "business"
INTENT_PERSONAL = "personal"
INTENT_QUESTION = "question"
INTENT_SPAM = "spam"
INTENT_GREETING = "greeting"

# Redis key prefixes (whatsapp: namespace to avoid collision with messenger:)
_COOLDOWN_KEY = "whatsapp:cooldown:{account_id}:{phone}"
_PAUSED_KEY = "whatsapp:paused:{account_id}:{phone}"
_CONFIG_KEY = "whatsapp:config:{account_id}:{phone}"
_DISCLOSED_KEY = "whatsapp:disclosed:{account_id}:{phone}"
_WINDOW_KEY = "whatsapp:window:{account_id}:{phone}"

# ChromaDB collection names
_BRAND_COLLECTION = "whatsapp_brand_knowledge"

DEFAULT_COOLDOWN_SECONDS = 300  # 5 minutes
DEFAULT_MEMORY_MESSAGES = 10  # Last 10 messages as context
CUSTOMER_SERVICE_WINDOW_HOURS = 24  # Meta 24-hour rule


# ── Redis helpers ────────────────────────────────────────────────────


async def _get_redis() -> Any:
    import redis.asyncio as aioredis
    url = getattr(settings, "MESSENGER_REDIS_URL", None) or settings.REDIS_URL
    return aioredis.from_url(url)


async def check_cooldown(account_id: str, phone: str, cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS) -> bool:
    """Return True if we can reply (cooldown has expired or was never set)."""
    try:
        r = await _get_redis()
        key = _COOLDOWN_KEY.format(account_id=account_id, phone=phone)
        ttl = await r.ttl(key)
        if ttl > 0:
            logger.debug("Cooldown active for %s/%s: %ds remaining", account_id, phone, ttl)
            return False
        return True
    except Exception:
        return True  # If Redis is down, allow reply


async def set_cooldown(account_id: str, phone: str, cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS) -> None:
    """Set the cooldown for a conversation after sending a reply."""
    try:
        r = await _get_redis()
        key = _COOLDOWN_KEY.format(account_id=account_id, phone=phone)
        await r.setex(key, cooldown_seconds, "1")
    except Exception:
        pass


async def is_thread_paused(account_id: str, phone: str) -> bool:
    """Check if human handoff is active for a specific phone number."""
    try:
        r = await _get_redis()
        key = _PAUSED_KEY.format(account_id=account_id, phone=phone)
        return bool(await r.exists(key))
    except Exception:
        return False


async def pause_thread(account_id: str, phone: str, reason: str = "human_handoff") -> None:
    """Pause the bot for a specific phone number (human handoff)."""
    try:
        r = await _get_redis()
        key = _PAUSED_KEY.format(account_id=account_id, phone=phone)
        await r.set(key, json.dumps({"reason": reason, "paused_at": datetime.now(UTC).isoformat()}))
    except Exception:
        pass


async def resume_thread(account_id: str, phone: str) -> None:
    """Resume the bot for a specific phone number."""
    try:
        r = await _get_redis()
        key = _PAUSED_KEY.format(account_id=account_id, phone=phone)
        await r.delete(key)
    except Exception:
        pass


# ── 24-hour customer service window ──────────────────────────────────


async def is_in_service_window(account_id: str, phone: str) -> bool:
    """Check if the 24-hour customer service window is still open.

    Meta allows free-form (service) messages only within 24 hours of the
    last inbound customer message. Outside the window, only pre-approved
    template messages can be sent.
    """
    try:
        r = await _get_redis()
        key = _WINDOW_KEY.format(account_id=account_id, phone=phone)
        ttl = await r.ttl(key)
        return ttl > 0
    except Exception:
        return True  # If Redis is down, assume window is open


async def refresh_service_window(account_id: str, phone: str) -> None:
    """Refresh the 24-hour customer service window timer.

    Called when a customer sends an inbound message. The window resets
    to 24 hours from the most recent inbound message.
    """
    try:
        r = await _get_redis()
        key = _WINDOW_KEY.format(account_id=account_id, phone=phone)
        await r.setex(key, CUSTOMER_SERVICE_WINDOW_HOURS * 3600, datetime.now(UTC).isoformat())
    except Exception:
        pass


async def get_window_remaining(account_id: str, phone: str) -> int:
    """Get seconds remaining in the 24-hour window (0 if expired)."""
    try:
        r = await _get_redis()
        key = _WINDOW_KEY.format(account_id=account_id, phone=phone)
        return await r.ttl(key)
    except Exception:
        return 0


# ── First-contact disclosure (Meta policy) ──────────────────────────


async def has_disclosed(account_id: str, phone: str) -> bool:
    """Check if the bot has already disclosed its automated nature.

    Meta's WhatsApp Business Messaging Policy requires that automated
    chat experiences disclose they are automated at the beginning of any
    conversation, after a significant lapse in time, or when a chat moves
    from human interaction to automated experience.
    """
    try:
        r = await _get_redis()
        key = _DISCLOSED_KEY.format(account_id=account_id, phone=phone)
        return bool(await r.exists(key))
    except Exception:
        return False


async def mark_disclosed(account_id: str, phone: str) -> None:
    """Mark that the bot has disclosed its automated nature.

    The key expires after 24 hours, so the bot re-discloses after a
    significant lapse in time (per Meta policy).
    """
    try:
        r = await _get_redis()
        key = _DISCLOSED_KEY.format(account_id=account_id, phone=phone)
        await r.setex(key, 86400, "1")  # 24-hour TTL
    except Exception:
        pass


# ── Per-conversation config ──────────────────────────────────────────


async def get_thread_config(account_id: str, phone: str) -> dict[str, Any]:
    """Get per-conversation config from Redis (overrides global config)."""
    try:
        r = await _get_redis()
        key = _CONFIG_KEY.format(account_id=account_id, phone=phone)
        data = await r.get(key)
        if data:
            return json.loads(data)
    except Exception:
        pass
    return {}


async def set_thread_config(account_id: str, phone: str, config: dict[str, Any]) -> None:
    """Set per-conversation config in Redis."""
    try:
        r = await _get_redis()
        key = _CONFIG_KEY.format(account_id=account_id, phone=phone)
        await r.set(key, json.dumps(config))
    except Exception:
        pass


# ── Conversation memory (ChromaDB) ────────────────────────────────────


async def store_message_memory(
    team_id: str,
    account_id: str,
    phone: str,
    sender: str,
    text: str,
) -> None:
    """Store a message in ChromaDB for conversation memory.

    Uses a per-phone-number collection so we can retrieve the last N
    messages for a specific conversation.
    """
    from app.services.chroma_client import _CHROMA_TIMEOUT, _collection_base_url, _get_collection_id, _get_embedding

    # Sanitize phone for collection name (digits only)
    safe_phone = "".join(c for c in phone if c.isdigit())
    col_name = f"whatsapp_{account_id}_{safe_phone}".replace("-", "_")
    doc_id = f"{datetime.now(UTC).timestamp()}_{sender}"

    embedding = await _get_embedding(text)
    if not embedding:
        return

    async with httpx.AsyncClient(timeout=_CHROMA_TIMEOUT) as client:
        try:
            col_id = await _get_collection_id(client, col_name)
            if not col_id:
                return
            await client.post(
                f"{_collection_base_url()}/{col_id}/add",
                json={
                    "ids": [doc_id],
                    "embeddings": [embedding],
                    "documents": [text],
                    "metadatas": [{"sender": sender, "phone": phone, "timestamp": datetime.now(UTC).isoformat()}],
                },
            )
        except Exception:
            pass  # ChromaDB unavailable; don't block


async def get_conversation_memory(
    account_id: str,
    phone: str,
    n_messages: int = DEFAULT_MEMORY_MESSAGES,
) -> list[dict[str, str]]:
    """Retrieve the last N messages for a conversation from ChromaDB.

    Returns a list of {"sender": "me"|"them", "text": "..."} dicts.
    """
    from app.services.chroma_client import _CHROMA_TIMEOUT, _collection_base_url, _get_collection_id

    safe_phone = "".join(c for c in phone if c.isdigit())
    col_name = f"whatsapp_{account_id}_{safe_phone}".replace("-", "_")

    async with httpx.AsyncClient(timeout=_CHROMA_TIMEOUT) as client:
        try:
            col_id = await _get_collection_id(client, col_name)
            if not col_id:
                return []
            resp = await client.post(
                f"{_collection_base_url()}/{col_id}/get",
                json={
                    "include": ["documents", "metadatas"],
                    "limit": n_messages,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                docs = data.get("documents", [])
                metas = data.get("metadatas", [])
                messages = []
                for i, doc in enumerate(docs):
                    meta = metas[i] if i < len(metas) else {}
                    messages.append({
                        "sender": meta.get("sender", "them"),
                        "text": doc,
                    })
                return messages
        except Exception:
            pass
    return []


# ── Brand knowledge RAG ──────────────────────────────────────────────


async def index_brand_knowledge(team_id: str, brand_data: dict) -> int:
    """Index brand information into ChromaDB for RAG retrieval.

    Stores brand DNA, positioning, values, services, and FAQs as
    searchable documents. Returns the number of documents indexed.
    """
    from app.services.chroma_client import _CHROMA_TIMEOUT, _collection_base_url, _get_collection_id, _get_embedding

    col_name = _BRAND_COLLECTION
    documents = []

    if brand_data.get("name"):
        documents.append(f"Brand name: {brand_data['name']}")
    if brand_data.get("tagline"):
        documents.append(f"Tagline: {brand_data['tagline']}")
    if brand_data.get("positioning_statement"):
        documents.append(f"Positioning: {brand_data['positioning_statement']}")
    if brand_data.get("mission"):
        documents.append(f"Mission: {brand_data['mission']}")
    if brand_data.get("industry"):
        documents.append(f"Industry: {brand_data['industry']}")
    if brand_data.get("values"):
        documents.append(f"Values: {', '.join(brand_data['values'])}")
    if brand_data.get("target_audience"):
        ta = brand_data["target_audience"]
        documents.append(f"Target audience: {ta.get('size', '')} {ta.get('profile', '')}")
    if brand_data.get("competitor_names"):
        documents.append(f"Competitors: {', '.join(brand_data['competitor_names'])}")

    indexed = 0
    async with httpx.AsyncClient(timeout=_CHROMA_TIMEOUT) as client:
        try:
            col_id = await _get_collection_id(client, col_name)
            if not col_id:
                return 0

            for i, doc in enumerate(documents):
                embedding = await _get_embedding(doc)
                if not embedding:
                    continue
                await client.post(
                    f"{_collection_base_url()}/{col_id}/add",
                    json={
                        "ids": [f"wa_brand_{i}"],
                        "embeddings": [embedding],
                        "documents": [doc],
                        "metadatas": [{"type": "brand", "team_id": team_id}],
                    },
                )
                indexed += 1
        except Exception as exc:
            logger.warning("Failed to index brand knowledge: %s", exc)

    return indexed


async def retrieve_brand_context(query: str, n_results: int = 3) -> str:
    """Retrieve relevant brand knowledge for a user query (RAG).

    Returns a formatted string with brand context to include in the
    system prompt.
    """
    from app.services.chroma_client import _CHROMA_TIMEOUT, _collection_base_url, _get_collection_id, _get_embedding

    embedding = await _get_embedding(query)
    if not embedding:
        return ""

    async with httpx.AsyncClient(timeout=_CHROMA_TIMEOUT) as client:
        try:
            col_id = await _get_collection_id(client, _BRAND_COLLECTION)
            if not col_id:
                return ""
            resp = await client.post(
                f"{_collection_base_url()}/{col_id}/query",
                json={"query_embeddings": [embedding], "n_results": n_results},
            )
            if resp.status_code == 200:
                docs = resp.json().get("documents", [[]])[0]
                if docs:
                    return "\n".join(f"- {d}" for d in docs)
        except Exception:
            pass
    return ""


# ── Intent detection ─────────────────────────────────────────────────


async def detect_intent(
    message: str,
    cf_token: str,
    cf_account: str,
    dmr_url: str,
) -> str:
    """Classify the intent of an inbound message.

    Returns one of: business, personal, question, spam, greeting.
    Uses DMR (local, primary) then CF Workers AI (cloud fallback).
    """
    prompt = f"""Classify the intent of this message into exactly one category:
- business: asking about services, pricing, products, business inquiry
- personal: personal conversation, catching up, social chat
- question: asking a factual question
- spam: promotional, scam, or unwanted content
- greeting: just saying hello or starting a conversation

Message: "{message[:200]}"

Reply with only the category name, nothing else."""

    # Try DMR first (local, free, primary)
    try:
        from app.services.dmr import call_dmr_chat
        result = await call_dmr_chat(
            prompt,
            max_tokens=10,
            temperature=0,
        )
        result_text = result.get("text", "").strip().lower()
        for intent in (INTENT_BUSINESS, INTENT_PERSONAL, INTENT_QUESTION, INTENT_SPAM, INTENT_GREETING):
            if intent in result_text:
                return intent
    except Exception as exc:
        logger.debug("DMR intent detection failed: %s", exc)

    # Fallback: CF Workers AI
    if cf_token and cf_account:
        try:
            url = f"https://api.cloudflare.com/client/v4/accounts/{cf_account}/ai/run/@cf/meta/llama-3.1-8b-instruct"
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {cf_token}"},
                    json={
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 10,
                        "temperature": 0,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    result = data.get("result", {}).get("response", "").strip().lower()
                    for intent in (INTENT_BUSINESS, INTENT_PERSONAL, INTENT_QUESTION, INTENT_SPAM, INTENT_GREETING):
                        if intent in result:
                            return intent
        except Exception:
            pass

    # Final fallback: simple keyword detection
    msg_lower = message.lower()
    if any(w in msg_lower for w in ["price", "cost", "service", "offer", "business", "company", "hire", "work", "τιμή", "κόστος", "υπηρεσία"]):
        return INTENT_BUSINESS
    if any(w in msg_lower for w in ["hello", "hi ", "hey", "γειά", "καλημέρα", "καλησπέρα", "γεια"]):
        return INTENT_GREETING
    if "?" in message or ";" in message:
        return INTENT_QUESTION
    if any(w in msg_lower for w in ["win", "free", "click", "subscribe", "promo", "δωρεάν"]):
        return INTENT_SPAM
    return INTENT_PERSONAL


# ── Context-aware reply generation ───────────────────────────────────


async def generate_contextual_reply(
    config: dict,
    user_message: str,
    account_name: str,
    account_id: str,
    phone: str,
    cf_token: str,
    cf_account: str,
    dmr_url: str,
    intent: str = "",
    brand_context: str = "",
    team_id: uuid.UUID | None = None,
) -> str:
    """Generate a context-aware AI reply with conversation memory and brand knowledge.

    Inference fallback chain (language-aware per AGENTS.md):
    1. Greek text → Cloudflare Workers AI (Llama 3.1 8B) — handles Greek correctly
       Fallback: DMR (local) → static text
    2. English/other text → DMR (Qwen3 8B, local, free) — primary
       Fallback: Cloudflare Workers AI → static text
    3. Final fallback: static text (with disclosure if first contact)

    Builds a rich system prompt that includes:
    - Base system prompt from config
    - Detected intent
    - Brand knowledge (RAG)
    - Conversation memory (last N messages)
    - Per-conversation overrides
    - Meta policy disclosure (first contact)
    """
    # Get per-conversation config overrides
    thread_config = await get_thread_config(account_id, phone)
    system_prompt = thread_config.get("system_prompt", config.get(
        "system_prompt",
        "You are a helpful assistant for {business_name}. Reply concisely and professionally.",
    )).replace("{business_name}", account_name).replace("{page_name}", account_name)

    max_tokens = thread_config.get("max_tokens", config.get("max_tokens", 300))
    temperature = thread_config.get("temperature", config.get("temperature", 0.7))
    fallback = config.get("fallback_text", "Thanks for your message! I'll get back to you soon.")

    # Deterministic safeguard: intercept pricing questions before the LLM
    # to prevent fabricated prices. The 8B model often invents specific euro
    # amounts despite instructions not to. This guarantees a safe, grounded
    # response for all pricing inquiries.
    if _is_pricing_question(user_message):
        disclosed = await has_disclosed(account_id, phone)
        if not disclosed:
            await mark_disclosed(account_id, phone)
        lang = "greek" if _is_greek_message(user_message) else "english"
        reply = _PRICING_RESPONSES[lang]
        await track_bot_reply(
            account_id, phone,
            provider="deterministic",
            model="pricing-guardrail",
            user_message=user_message,
            reply_text=reply,
            guardrail_triggered="pricing",
            language=lang,
            intent=intent or "",
        )
        return reply

    # Build enhanced system prompt
    enhanced_prompt = system_prompt

    # Inject brand voice rules from the brand system so the WhatsApp bot
    # also enforces banned phrases, euro pricing, and bilingual matching.
    from app.services.messenger_chatbot import _get_brand_voice_block
    brand_voice_block = await _get_brand_voice_block()
    if brand_voice_block and brand_voice_block not in enhanced_prompt:
        enhanced_prompt += brand_voice_block

    if intent:
        intent_guidance = {
            INTENT_BUSINESS: "This is a business inquiry. Be professional and informative. Mention Cloudless.gr services if relevant.",
            INTENT_PERSONAL: "This is a personal conversation. Be friendly and casual.",
            INTENT_QUESTION: "This is a question. Answer concisely and accurately.",
            INTENT_SPAM: "This appears to be spam. Respond politely but briefly.",
            INTENT_GREETING: "This is a greeting. Respond warmly and ask how you can help.",
        }
        enhanced_prompt += f"\n\nIntent: {intent_guidance.get(intent, '')}"

    if brand_context:
        enhanced_prompt += f"\n\nBrand context:\n{brand_context}"

    # Get conversation memory
    memory = await get_conversation_memory(account_id, phone)
    if memory:
        memory_text = "\n".join(f"{'You' if m['sender'] == 'me' else 'Them'}: {m['text'][:100]}" for m in memory[-5:])
        enhanced_prompt += f"\n\nRecent conversation:\n{memory_text}"

    enhanced_prompt += "\n\nReply naturally in the same language as the user's message. Keep it short and conversational."

    # Check if we need to disclose the bot's automated nature (Meta policy)
    disclosed = await has_disclosed(account_id, phone)
    disclosure_prefix = ""
    if not disclosed:
        disclosure_prefix = "🤖 "
        enhanced_prompt += (
            "\n\nIMPORTANT: This is your first message in this conversation. "
            "You must start your reply by briefly disclosing that you are an "
            "automated assistant (e.g. 'Hi! This is an automated reply on behalf "
            "of Cloudless.'). Keep it natural and brief, then proceed with your "
            "normal response."
        )

    # Model routing for bot replies:
    # Cloudflare Workers AI (Llama 3.1 8B) is the primary for bot replies because
    # it follows system prompts much better than DMR Qwen3 8B (pricing rules,
    # bot disclosure, language matching). CF Workers AI has a generous free tier.
    # DMR (local, free, private) is the fallback when CF is unavailable.

    cf_start = time.perf_counter()
    # 1. Try Cloudflare Workers AI first (best prompt adherence, handles Greek + English)
    model = config.get("model", "@cf/meta/llama-3.1-8b-instruct")
    if cf_token and cf_account:
        try:
            url = f"https://api.cloudflare.com/client/v4/accounts/{cf_account}/ai/run/{model}"
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {cf_token}"},
                    json={
                        "messages": [
                            {"role": "system", "content": enhanced_prompt},
                            {"role": "user", "content": user_message},
                        ],
                        "max_tokens": max_tokens,
                        "temperature": temperature,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("result") and data["result"].get("response"):
                        text = data["result"]["response"].strip()
                        if text:
                            if not disclosed:
                                await mark_disclosed(account_id, phone)
                            reply = f"{disclosure_prefix}{text}" if not disclosed else text
                            await track_bot_reply(
                                account_id, phone,
                                provider="cloudflare", model=model,
                                user_message=user_message,
                                reply_text=reply,
                                latency_ms=int(
                                    (time.perf_counter() - cf_start) * 1000
                                ),
                                intent=intent,
                                language="greek" if any(
                                    0x0370 <= ord(c) <= 0x03FF
                                    for c in user_message
                                ) else "english",
                                team_id=team_id,
                            )
                            return reply
        except Exception as exc:
            logger.warning("Cloudflare AI bot reply failed: %s", exc)
            await track_bot_reply(
                account_id, phone,
                provider="cloudflare", model=model,
                user_message=user_message, reply_text="",
                latency_ms=int(
                    (time.perf_counter() - cf_start) * 1000
                ),
                success=False, error=str(exc), intent=intent,
                team_id=team_id,
            )

    # 2. Fallback: DMR (local, free, private)
    dmr_start = time.perf_counter()
    try:
        from app.services.dmr import call_dmr_chat
        result = await call_dmr_chat(
            user_message,
            system=enhanced_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        text = result.get("text", "").strip()
        if text:
            if not disclosed:
                await mark_disclosed(account_id, phone)
            reply = f"{disclosure_prefix}{text}" if not disclosed else text
            await track_bot_reply(
                account_id, phone,
                provider="dmr", model=settings.DMR_TEXT_MODEL,
                user_message=user_message, reply_text=reply,
                latency_ms=int(
                    (time.perf_counter() - dmr_start) * 1000
                ),
                intent=intent,
                language="greek" if any(
                    0x0370 <= ord(c) <= 0x03FF
                    for c in user_message
                ) else "english",
                team_id=team_id,
            )
            return reply
    except Exception as exc:
        logger.warning("DMR chatbot reply failed: %s", exc)
        await track_bot_reply(
            account_id, phone,
            provider="dmr", model=settings.DMR_TEXT_MODEL,
            user_message=user_message, reply_text="",
            latency_ms=int(
                (time.perf_counter() - dmr_start) * 1000
            ),
            success=False, error=str(exc), intent=intent,
            team_id=team_id,
        )

    # 3. Final fallback: static text (with disclosure if first contact)
    if not disclosed:
        await mark_disclosed(account_id, phone)
        return f"{disclosure_prefix}{fallback}"
    return fallback


# ── Full message processing pipeline ─────────────────────────────────


async def process_inbound_message(
    account_id: str,
    team_id: str,
    phone: str,
    sender_name: str,
    message_text: str,
    message_id: str,
    config: dict,
    account_name: str,
) -> dict[str, Any]:
    """Full inbound message processing pipeline.

    This is the main entry point called by the webhook handler. It:
    1. Refreshes the 24-hour customer service window
    2. Stores the message in conversation memory (ChromaDB)
    3. Checks if auto-reply is enabled
    4. Checks per-conversation cooldown
    5. Checks if thread is paused (human handoff)
    6. Detects message intent
    7. Retrieves brand context (RAG)
    8. Generates a context-aware AI reply
    9. Sets the cooldown
    10. Returns the reply text (caller sends it via Cloud API)

    Returns:
        dict with keys:
        - reply: str | None — the generated reply (None if skipped)
        - skipped: bool — True if reply was skipped
        - reason: str — why it was skipped (if applicable)
        - intent: str — detected intent
        - in_window: bool — whether 24h window was active
    """
    result: dict[str, Any] = {
        "reply": None,
        "skipped": False,
        "reason": "",
        "intent": "",
        "in_window": True,
    }

    # 1. Refresh the 24-hour customer service window
    await refresh_service_window(account_id, phone)

    # 2. Store the inbound message in conversation memory
    await store_message_memory(team_id, account_id, phone, "them", message_text)

    # 3. Check if auto-reply is enabled
    if not config.get("enabled", False):
        result["skipped"] = True
        result["reason"] = "auto_reply_disabled"
        return result

    # 4. Check per-conversation cooldown
    cooldown_seconds = config.get("cooldown_seconds", DEFAULT_COOLDOWN_SECONDS)
    if not await check_cooldown(account_id, phone, cooldown_seconds):
        result["skipped"] = True
        result["reason"] = "cooldown_active"
        return result

    # 5. Check if thread is paused (human handoff)
    if await is_thread_paused(account_id, phone):
        result["skipped"] = True
        result["reason"] = "thread_paused"
        return result

    # 6. Detect message intent
    cf_token = os.getenv("CLOUDFLARE_API_TOKEN", "")
    cf_account = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
    dmr_url = os.getenv("DMR_BASE_URL", "http://localhost:12434")

    intent = await detect_intent(message_text, cf_token, cf_account, dmr_url)
    result["intent"] = intent

    # Skip spam if configured to not reply
    if intent == INTENT_SPAM and not config.get("reply_to_spam", False):
        result["skipped"] = True
        result["reason"] = "spam_not_replied"
        return result

    # Skip greetings if configured to not reply
    if intent == INTENT_GREETING and not config.get("reply_to_greetings", True):
        result["skipped"] = True
        result["reason"] = "greetings_not_replied"
        return result

    # 7. Retrieve brand context (RAG)
    brand_context = await retrieve_brand_context(message_text)

    # 8. Generate a context-aware AI reply
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
        phone=phone,
        cf_token=cf_token,
        cf_account=cf_account,
        dmr_url=dmr_url,
        intent=intent,
        brand_context=brand_context,
        team_id=team_uuid,
    )

    # 9. Set the cooldown
    await set_cooldown(account_id, phone, cooldown_seconds)

    # 10. Store our reply in conversation memory
    await store_message_memory(team_id, account_id, phone, "me", reply_text)

    result["reply"] = reply_text
    return result
