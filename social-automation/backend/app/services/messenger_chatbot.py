"""Personal Messenger chatbot service.

Provides conversation memory, brand knowledge RAG, intent detection,
per-conversation configuration, human handoff, and rate-limit-aware cooldowns
for the personal Messenger auto-reply system.

Services used (free/open-source-first):
    - Cloudflare Workers AI (Llama 3.1 8B) — primary reply generation (Greek + English)
    - Docker Model Runner (Qwen3 8B, local) — fallback reply generation + intent detection
    - ChromaDB (local) / Cloudflare Vectorize (cloud) — conversation memory + brand RAG
    - Redis — per-conversation cooldown + paused-thread tracking
    - PostgreSQL — per-conversation config + seen state
    - Brand Voice API — banned phrases, preferred phrases, euro pricing, bilingual rules

Architecture:
    1. Deterministic safeguard: intercept pricing questions with keyword detection
       and return a hardcoded response (prevents LLM price hallucination)
    2. For each inbound message, detect intent (business, personal, spam, question, greeting)
    3. Retrieve conversation memory (last 10 messages from ChromaDB)
    4. Retrieve brand knowledge (RAG from ChromaDB/Vectorize)
    5. Build a context-aware prompt with brand voice + memory + intent
    6. Inject brand voice rules from the Brand system API (banned phrases,
       preferred phrases, euro pricing, bilingual language matching, bot disclosure)
    7. Generate reply via unified routing:
       - Cloudflare Workers AI (Llama 3.1 8B) — primary for all languages
       - DMR (Qwen3 8B, local, free) — fallback when CF is unavailable
       - Static text — final fallback (with bot disclosure)
    8. Check per-conversation cooldown (Redis, default 5 min)
    9. Check per-conversation pause (human handoff)
   10. Send reply and store in conversation memory
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


async def track_bot_reply(
    account_id: str,
    thread_id: str,
    *,
    provider: str,
    model: str,
    user_message: str,
    reply_text: str,
    latency_ms: int | None = None,
    success: bool = True,
    error: str | None = None,
    intent: str = "",
    guardrail_triggered: str = "",
    language: str = "",
    team_id: uuid.UUID | None = None,
) -> None:
    """Log a bot reply to ai_usage_logs and analytics_events (non-blocking)."""
    try:
        from app.services.usage_tracker import track_inference
        await track_inference(
            provider=provider, model=model,
            prompt=user_message[:500],
            team_id=team_id, endpoint="bot_reply",
            latency_ms=latency_ms, success=success, error=error,
            meta_data={
                "account_id": account_id,
                "thread_id": thread_id,
                "reply_length": len(reply_text),
                "intent": intent,
                "guardrail": guardrail_triggered,
                "language": language,
            },
        )
    except Exception as exc:
        logger.debug("Failed to track bot AI usage: %s", exc)

    if team_id:
        try:
            from app.db.session import async_session_maker
            from app.models.analytics import AnalyticsEvent
            event = AnalyticsEvent(
                team_id=team_id,
                social_account_id=(
                    uuid.UUID(account_id)
                    if _is_valid_uuid(account_id) else None
                ),
                event_type="bot_reply",
                platform="messenger",
                occurred_at=datetime.now(UTC),
                meta_data={
                    "thread_id": thread_id,
                    "provider": provider,
                    "model": model,
                    "success": success,
                    "error": error,
                    "intent": intent,
                    "guardrail": guardrail_triggered,
                    "language": language,
                    "reply_length": len(reply_text),
                    "latency_ms": latency_ms,
                },
            )
            async with async_session_maker() as s:
                s.add(event)
                await s.commit()
        except Exception as exc:
            logger.debug("Failed to track bot analytics event: %s", exc)


def _is_valid_uuid(val: str) -> bool:
    """Check if a string is a valid UUID."""
    try:
        uuid.UUID(val)
        return True
    except (ValueError, AttributeError):
        return False


settings = get_settings()

# Intent categories
INTENT_BUSINESS = "business"
INTENT_PERSONAL = "personal"
INTENT_QUESTION = "question"
INTENT_SPAM = "spam"
INTENT_GREETING = "greeting"

# Redis key prefixes
_COOLDOWN_KEY = "messenger:cooldown:{account_id}:{thread_id}"
_PAUSED_KEY = "messenger:paused:{account_id}:{thread_id}"
_CONFIG_KEY = "messenger:config:{account_id}:{thread_id}"
_DISCLOSED_KEY = "messenger:disclosed:{account_id}:{thread_id}"

# ChromaDB collection names
_BRAND_COLLECTION = "messenger_brand_knowledge"

DEFAULT_COOLDOWN_SECONDS = 300  # 5 minutes
DEFAULT_MEMORY_MESSAGES = 10  # Last 10 messages as context


# ── Redis helpers ────────────────────────────────────────────────────


async def _get_redis() -> Any:
    import redis.asyncio as aioredis
    # Use dedicated Redis DB for bot state (cooldowns, pause, config)
    # Falls back to main REDIS_URL if MESSENGER_REDIS_URL is not set
    url = getattr(settings, "MESSENGER_REDIS_URL", None) or settings.REDIS_URL
    return aioredis.from_url(url)


async def check_cooldown(account_id: str, thread_id: str, cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS) -> bool:
    """Return True if we can reply (cooldown has expired or was never set)."""
    try:
        r = await _get_redis()
        key = _COOLDOWN_KEY.format(account_id=account_id, thread_id=thread_id)
        ttl = await r.ttl(key)
        if ttl > 0:
            logger.debug("Cooldown active for %s/%s: %ds remaining", account_id, thread_id, ttl)
            return False
        return True
    except Exception:
        return True  # If Redis is down, allow reply


async def set_cooldown(account_id: str, thread_id: str, cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS) -> None:
    """Set the cooldown for a conversation after sending a reply."""
    try:
        r = await _get_redis()
        key = _COOLDOWN_KEY.format(account_id=account_id, thread_id=thread_id)
        await r.setex(key, cooldown_seconds, "1")
    except Exception:
        pass


async def is_thread_paused(account_id: str, thread_id: str) -> bool:
    """Check if human handoff is active for a specific thread."""
    try:
        r = await _get_redis()
        key = _PAUSED_KEY.format(account_id=account_id, thread_id=thread_id)
        return bool(await r.exists(key))
    except Exception:
        return False


async def pause_thread(account_id: str, thread_id: str, reason: str = "human_handoff") -> None:
    """Pause the bot for a specific thread (human handoff)."""
    try:
        r = await _get_redis()
        key = _PAUSED_KEY.format(account_id=account_id, thread_id=thread_id)
        await r.set(key, json.dumps({"reason": reason, "paused_at": datetime.now(UTC).isoformat()}))
    except Exception:
        pass


async def resume_thread(account_id: str, thread_id: str) -> None:
    """Resume the bot for a specific thread."""
    try:
        r = await _get_redis()
        key = _PAUSED_KEY.format(account_id=account_id, thread_id=thread_id)
        await r.delete(key)
    except Exception:
        pass


# ── First-contact disclosure (Meta policy) ──────────────────────────


async def has_disclosed(account_id: str, thread_id: str) -> bool:
    """Check if the bot has already disclosed its automated nature in this thread.

    Meta's Messenger Platform policy requires that automated chat experiences
    disclose they are automated:
    - at the beginning of any conversation or message thread,
    - after a significant lapse of time, or
    - when a chat moves from human interaction to automated experience.
    """
    try:
        r = await _get_redis()
        key = _DISCLOSED_KEY.format(account_id=account_id, thread_id=thread_id)
        return bool(await r.exists(key))
    except Exception:
        return False


async def mark_disclosed(account_id: str, thread_id: str) -> None:
    """Mark that the bot has disclosed its automated nature in this thread.

    The key expires after 24 hours, so the bot re-discloses after a significant
    lapse of time (per Meta policy).
    """
    try:
        r = await _get_redis()
        key = _DISCLOSED_KEY.format(account_id=account_id, thread_id=thread_id)
        await r.setex(key, 86400, "1")  # 24-hour TTL
    except Exception:
        pass


# ── Per-conversation config ──────────────────────────────────────────


async def get_thread_config(account_id: str, thread_id: str) -> dict[str, Any]:
    """Get per-conversation config from Redis (overrides global config)."""
    try:
        r = await _get_redis()
        key = _CONFIG_KEY.format(account_id=account_id, thread_id=thread_id)
        data = await r.get(key)
        if data:
            return json.loads(data)
    except Exception:
        pass
    return {}


async def set_thread_config(account_id: str, thread_id: str, config: dict[str, Any]) -> None:
    """Set per-conversation config in Redis."""
    try:
        r = await _get_redis()
        key = _CONFIG_KEY.format(account_id=account_id, thread_id=thread_id)
        await r.set(key, json.dumps(config))
    except Exception:
        pass


# ── Conversation memory (ChromaDB) ────────────────────────────────────


async def store_message_memory(
    team_id: str,
    account_id: str,
    thread_id: str,
    sender: str,
    text: str,
) -> None:
    """Store a message in ChromaDB for conversation memory.

    Uses a per-thread collection so we can retrieve the last N messages
    for a specific conversation.
    """
    from app.services.chroma_client import _CHROMA_TIMEOUT, _collection_base_url, _get_collection_id, _get_embedding

    # Combine account + thread for a unique collection name
    col_name = f"messenger_{account_id}_{thread_id}".replace("-", "_")
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
                    "metadatas": [{"sender": sender, "thread_id": thread_id, "timestamp": datetime.now(UTC).isoformat()}],
                },
            )
        except Exception:
            pass  # ChromaDB unavailable; don't block


async def get_conversation_memory(
    account_id: str,
    thread_id: str,
    n_messages: int = DEFAULT_MEMORY_MESSAGES,
) -> list[dict[str, str]]:
    """Retrieve the last N messages for a conversation from ChromaDB.

    Returns a list of {"sender": "me"|"them", "text": "..."} dicts.
    """
    from app.services.chroma_client import _CHROMA_TIMEOUT, _collection_base_url, _get_collection_id

    col_name = f"messenger_{account_id}_{thread_id}".replace("-", "_")

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

    # Build documents from brand data
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

    # Index each document
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
                        "ids": [f"brand_{i}"],
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
    if any(w in msg_lower for w in ["price", "cost", "service", "offer", "business", "company", "hire", "work"]):
        return INTENT_BUSINESS
    if any(w in msg_lower for w in ["hello", "hi ", "hey", "γειά", "καλημέρα", "καλησπέρα"]):
        return INTENT_GREETING
    if "?" in message:
        return INTENT_QUESTION
    if any(w in msg_lower for w in ["win", "free", "click", "subscribe", "promo"]):
        return INTENT_SPAM
    return INTENT_PERSONAL


# ── Brand voice injection ────────────────────────────────────────────

_brand_voice_cache: dict = {}
_brand_voice_cache_ts: float = 0
_BRAND_VOICE_TTL = 300  # 5 minutes


async def _get_brand_voice_block() -> str:
    """Fetch brand voice rules from the brand system and return a text block
    to append to bot system prompts.

    Caches the result for 5 minutes to avoid hitting the API on every message.
    Returns an empty string if the brand system is unavailable.
    """
    import time

    global _brand_voice_cache, _brand_voice_cache_ts
    now = time.time()
    if _brand_voice_cache and (now - _brand_voice_cache_ts) < _BRAND_VOICE_TTL:
        return _brand_voice_cache.get("block", "")

    try:
        import os

        import httpx

        api_base = os.getenv("SOCIAL_API_BASE", "http://social-api:8000")
        async with httpx.AsyncClient(timeout=5) as client:
            # Authenticate to get a token (admin credentials from env)
            admin_email = os.getenv("SOCIAL_ADMIN_EMAIL", "")
            admin_password = os.getenv("SOCIAL_ADMIN_PASSWORD", "")
            headers = {}
            if admin_email and admin_password:
                auth_resp = await client.post(
                    f"{api_base}/api/v1/auth/login",
                    data={"username": admin_email, "password": admin_password},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                if auth_resp.status_code == 200:
                    token = auth_resp.json().get("access_token", "")
                    if token:
                        headers["Authorization"] = f"Bearer {token}"

            resp = await client.get(f"{api_base}/api/v1/brand/voice", headers=headers)
            if resp.status_code != 200:
                return ""
            data = resp.json()

        banned = data.get("banned_phrases", [])
        preferred = data.get("preferred_phrases", [])
        sig = data.get("voice_signature", {})

        lines = ["\n\nBRAND VOICE RULES:"]
        if preferred:
            lines.append(f"- Use: {', '.join(preferred[:8])}")
        if banned:
            lines.append(f"- Never use: {', '.join(banned[:8])}")
        if sig.get("pricing_currency") == "EUR":
            lines.append(
                "- Always quote prices in euros (€). Never use dollars or USD. "
                "Do NOT make up specific prices. If asked about pricing, say: "
                "\"Get started at cloudless.gr for a free audit!\""
            )
        if sig.get("language"):
            lines.append(
                "- You must support both Greek and English. Always match the user "
                "language exactly. If the user writes in Greek, reply in Greek. "
                "If in English, reply in English. Never mix languages."
            )
        if sig.get("disclosure"):
            lines.append("- Always say you are a bot. Be honest.")
        lines.append("- Be warm, direct, confident. Keep it 1-3 sentences.")

        block = "\n".join(lines)
        _brand_voice_cache = {"block": block}
        _brand_voice_cache_ts = now
        return block
    except Exception:
        return ""


# ── Deterministic safeguards ─────────────────────────────────────────

# Keywords that indicate a pricing question
_PRICING_KEYWORDS = [
    # English
    "how much", "price", "pricing", "cost", "costs", "expensive", "cheap",
    "budget", "quote", "rate", "rates", "fee", "fees", "plan", "plans",
    "subscription", "tier", "tiers", "per month", "per year",
    # Greek
    "πόσο", "ποσό", "τιμή", "τιμές", "κόστος", "κόστους", "ακριβό",
    "φθηνό", "προϋπολογισμός", "παράθεμα", "συνδρομή", "συνδρομές",
    "τιμολόγιο", "χρέωση", "χρεώσεις",
]

# Deterministic pricing responses (Greek + English)
_PRICING_RESPONSES = {
    "greek": (
        "🤖 Γεια! Είμαι το Cloudless bot. Η τιμή εξαρτάται από τις ανάγκες σας "
        "— κάθε έργο είναι διαφορετικό. Μπορείτε να ξεκινήσετε με δωρεάν "
        "αξιολόγηση στο cloudless.gr. Τι είδους υπηρεσία σας ενδιαφέρει; "
        "(cloud, αυτοματοποίηση, social media)"
    ),
    "english": (
        "🤖 Hi! I'm the Cloudless bot. Pricing depends on your needs — "
        "every project is different. You can start with a free audit at "
        "cloudless.gr. What kind of service are you interested in? "
        "(cloud, automation, social media)"
    ),
}


def _is_pricing_question(message: str) -> bool:
    """Check if a message is asking about pricing."""
    msg_lower = message.lower()
    return any(kw in msg_lower for kw in _PRICING_KEYWORDS)


def _is_greek_message(message: str) -> bool:
    """Check if a message contains Greek characters."""
    return any(0x0370 <= ord(c) <= 0x03FF or 0x1F00 <= ord(c) <= 0x1FFF
               for c in message)


# Steering questions appended when the LLM reply doesn't end with one.
_STEERING_QUESTIONS_EN = [
    " What would you like to know more about?",
    " Can you tell me more about your needs?",
    " Would you like to explore how we can help?",
]
_STEERING_QUESTIONS_GR = [
    " Τι θα θέλατε να μάθετε περισσότερα;",
    " Μπορείτε να μου πείτε περισσότερα για τις ανάγκες σας;",
    " Θέλετε να δούμε πώς μπορούμε να σας βοηθήσουμε;",
]


def _ensure_steering_question(text: str, is_greek: bool) -> str:
    """Append a steering question if the reply doesn't end with one."""
    if not text:
        return text
    stripped = text.rstrip()
    if stripped.endswith("?") or stripped.endswith(";"):
        return text
    questions = _STEERING_QUESTIONS_GR if is_greek else _STEERING_QUESTIONS_EN
    import random
    return stripped + random.choice(questions)


# ── Context-aware reply generation ───────────────────────────────────


async def generate_contextual_reply(
    config: dict,
    user_message: str,
    account_name: str,
    account_id: str,
    thread_id: str,
    cf_token: str,
    cf_account: str,
    dmr_url: str,
    intent: str = "",
    brand_context: str = "",
    team_id: uuid.UUID | None = None,
) -> str:
    """Generate a context-aware AI reply with conversation memory and brand knowledge.

    Inference fallback chain (unified, Cloudflare-first):
    1. Cloudflare Workers AI (Llama 3.1 8B) — primary for all languages
       Fallback: DMR (local) → static text
    2. DMR (Qwen3 8B, local, free) — fallback when CF is unavailable
       Fallback: static text
    3. Final fallback: static text (with disclosure if first contact)

    Deterministic safeguards (before LLM):
    - Pricing questions are intercepted by keyword detection and return
      a hardcoded response directing users to cloudless.gr for a free audit.
      This prevents the 8B model from hallucinating specific euro amounts.

    Brand voice injection:
    - Fetches banned phrases, preferred phrases, pricing currency, language
      rules, and bot disclosure from the Brand system API.
    - Caches the brand voice block for 5 minutes to avoid hitting the API
      on every message.
    - Injects the block into the system prompt even if the per-account
      system_prompt doesn't include brand voice rules explicitly.

    Builds a rich system prompt that includes:
    - Base system prompt from config
    - Brand voice rules (from Brand system API)
    - Detected intent
    - Brand knowledge (RAG)
    - Conversation memory (last N messages)
    """
    # Get per-conversation config overrides
    thread_config = await get_thread_config(account_id, thread_id)
    system_prompt = thread_config.get("system_prompt", config.get(
        "system_prompt",
        "You are a helpful assistant for {page_name}. Reply concisely and professionally.",
    )).replace("{page_name}", account_name)

    max_tokens = thread_config.get("max_tokens", config.get("max_tokens", 200))
    temperature = thread_config.get("temperature", 0.7)
    fallback = config.get("fallback_text", "Thanks for your message! I'll get back to you soon.")

    # Deterministic safeguard: intercept pricing questions before the LLM
    # to prevent fabricated prices. The 8B model often invents specific euro
    # amounts despite instructions not to. This guarantees a safe, grounded
    # response for all pricing inquiries.
    if _is_pricing_question(user_message):
        disclosed = await has_disclosed(account_id, thread_id)
        if not disclosed:
            await mark_disclosed(account_id, thread_id)
        lang = "greek" if _is_greek_message(user_message) else "english"
        reply = _ensure_steering_question(_PRICING_RESPONSES[lang], lang == "greek")
        await track_bot_reply(
            account_id, thread_id,
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

    # Fetch brand voice rules from the brand system and inject them
    # so ALL bots enforce banned phrases, preferred phrases, euro pricing,
    # and bilingual language matching — even if the per-account system_prompt
    # doesn't include them explicitly.
    brand_voice_block = await _get_brand_voice_block()
    if brand_voice_block and brand_voice_block not in enhanced_prompt:
        enhanced_prompt += brand_voice_block

    if intent:
        intent_guidance = {
            INTENT_BUSINESS: (
                "This is a business inquiry. Be professional and informative. "
                "Ask a follow-up question to understand their needs better. "
                "Mention relevant Cloudless.gr services (cloud infrastructure, automation, "
                "social media management). Steer them toward booking a free consultation "
                "at cloudless.gr or emailing hello@cloudless.gr."
            ),
            INTENT_PERSONAL: (
                "This is a personal conversation. Be friendly and casual. "
                "If they mention a business need, gently steer toward Cloudless.gr."
            ),
            INTENT_QUESTION: (
                "This is a question. Answer concisely and accurately. "
                "If the question is about services or pricing, steer them to "
                "cloudless.gr for a free audit. Ask what they need help with."
            ),
            INTENT_SPAM: "This appears to be spam. Respond politely but briefly.",
            INTENT_GREETING: (
                "This is a greeting. Respond warmly, introduce yourself as the Cloudless bot, "
                "and ask what they're interested in — services, pricing, or just chatting. "
                "Steer the conversation toward understanding their needs."
            ),
        }
        enhanced_prompt += f"\n\nIntent: {intent_guidance.get(intent, '')}"

    if brand_context:
        enhanced_prompt += f"\n\nBrand context:\n{brand_context}"

    # Get conversation memory
    memory = await get_conversation_memory(account_id, thread_id)
    if memory:
        memory_text = "\n".join(f"{'You' if m['sender'] == 'me' else 'Them'}: {m['text'][:100]}" for m in memory[-5:])
        enhanced_prompt += f"\n\nRecent conversation:\n{memory_text}"

    # Detect language programmatically and enforce it
    is_greek = _is_greek_message(user_message)
    detected_lang = "Greek" if is_greek else "English"

    enhanced_prompt += (
        f"\n\nCRITICAL LANGUAGE RULE: The user's message is in {detected_lang}. "
        f"You MUST reply ONLY in {detected_lang}. "
        f"Do NOT mix languages. Do NOT translate to another language. "
        f"Do NOT add translations or parenthetical text in other languages. "
        f"Reply in {detected_lang} from the first word to the last.\n\n"
        "Reply naturally. Keep it short and conversational. "
        "Always end with a question to keep the conversation going and "
        "guide the customer toward the next step (booking, consultation, "
        "or providing more details about their needs).\n\n"
        "Example good replies:\n"
        "User: Hi, what do you offer?\n"
        "You: Hi! I'm the Cloudless bot. We offer cloud, AI, and custom "
        "software for startups. What are you most interested in?\n"
        "User: I need help with my infrastructure\n"
        "You: Great! We specialize in cloud architecture. Do you have "
        "existing infrastructure or starting from scratch? What exactly "
        "do you need?"
    )

    # Add Greek-specific steering and quality instructions when the user
    # writes in Greek. The 8B model often ignores English instructions for
    # Greek replies, so we repeat the key rules in Greek.
    if is_greek:
        enhanced_prompt += (
            "\n\nΟΔΗΓΙΕΣ ΓΙΑ ΕΛΛΗΝΙΚΑ (Greek instructions — follow strictly):\n"
            "1. ΑΠΑΝΤΑ ΜΟΝΟ ΣΤΑ ΕΛΛΗΝΙΚΑ. Μην αναμειγνύεις γλώσσες.\n"
            "2. Η απάντηση πρέπει να τελειώνει ΠΑΝΤΑ με ερώτηση (;) για να "
            "συνεχιστεί η συζήτηση και να καθοδηγηθεί ο πελάτης.\n"
            "3. Γράφε σωστά ελληνικά — όχι ακατανόητες λέξεις ή μεταφράσεις.\n"
            "4. Ναι είσαι bot. Να το αναφέρεις φυσικά.\n"
            "5. Τιμές μόνο σε ευρώ (€). Μην καταχωρείς συγκεκριμένες τιμές.\n"
            "6. Κράτα το σύντομο: 1-3 προτάσεις + μία ερώτηση στο τέλος.\n\n"
            "Παραδείγματα σωστών απαντήσεων:\n"
            "Χρήστης: Γεια, τι προσφέρετε;\n"
            "Εσύ: Γεια σας! Είμαι το Cloudless bot. Προσφέρουμε cloud, AI και "
            "custom software για startups. Τι σας ενδιαφέρει περισσότερο;\n"
            "Χρήστης: Θέλω βοήθεια με το cloud\n"
            "Εσύ: Υπέροχα! Ειδικευόμαστε σε cloud architecture. Έχετε ήδη "
            "υποδομή ή ξεκινάτε από το μηδέν; Τι ακριβώς χρειάζεστε;"
        )

    # Check if we need to disclose the bot's automated nature (Meta policy)
    disclosed = await has_disclosed(account_id, thread_id)
    disclosure_prefix = ""
    if not disclosed:
        disclosure_prefix = "🤖 Auto-reply: "
        # Add disclosure instruction to the prompt
        enhanced_prompt += (
            "\n\nIMPORTANT: This is your first message in this conversation. "
            "You must start your reply by briefly disclosing that you are an "
            "automated assistant (e.g. 'Hi! This is an automated reply on behalf "
            "of Themis.'). Keep it natural and brief, then proceed with your "
            "normal response."
        )

    # Model routing for bot replies:
    # Cloudflare Workers AI (Llama 3.1 8B) is the primary for bot replies because
    # it follows system prompts much better than DMR Qwen3 8B (recruiting mode,
    # pricing rules, bot disclosure). CF Workers AI has a generous free tier.
    # DMR (local, free, private) is the fallback when CF is unavailable.
    # Strategy: try CF first (both Greek and English), fall back to DMR, then static.

    cf_start = time.perf_counter()
    # 1. Try Cloudflare Workers AI first (best prompt adherence, handles Greek + English)
    model = config.get("model", "@cf/meta/llama-3.1-8b-instruct")
    if cf_token and cf_account:
        try:
            url = f"https://api.cloudflare.com/client/v4/accounts/{cf_account}/ai/run/{model}"
            async with httpx.AsyncClient(timeout=30) as client:
                # Prepend language instruction to the user message so the LLM
                # sees it in context, not just the system prompt
                lang_instruction = f"[Reply in {detected_lang} only] {user_message}"
                resp = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {cf_token}"},
                    json={
                        "messages": [
                            {"role": "system", "content": enhanced_prompt},
                            {"role": "user", "content": lang_instruction},
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
                            # Ensure Greek replies end with a steering question
                            text = _ensure_steering_question(text, is_greek)
                            if not disclosed:
                                await mark_disclosed(account_id, thread_id)
                            reply = f"{disclosure_prefix}{text}" if not disclosed else text
                            await track_bot_reply(
                                account_id, thread_id,
                                provider="cloudflare", model=model,
                                user_message=user_message,
                                reply_text=reply,
                                latency_ms=int(
                                    (time.perf_counter() - cf_start) * 1000
                                ),
                                intent=intent,
                                language="greek" if _is_greek_message(user_message) else "english",
                                team_id=team_id,
                            )
                            return reply
        except Exception as exc:
            logger.warning("Cloudflare AI bot reply failed: %s", exc)
            await track_bot_reply(
                account_id, thread_id,
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
            lang_instruction,
            system=enhanced_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        text = result.get("text", "").strip()
        if text:
            # Ensure Greek replies end with a steering question
            text = _ensure_steering_question(text, is_greek)
            if not disclosed:
                await mark_disclosed(account_id, thread_id)
            reply = f"{disclosure_prefix}{text}" if not disclosed else text
            await track_bot_reply(
                account_id, thread_id,
                provider="dmr", model=settings.DMR_TEXT_MODEL,
                user_message=user_message, reply_text=reply,
                latency_ms=int(
                    (time.perf_counter() - dmr_start) * 1000
                ),
                intent=intent,
                language="greek" if _is_greek_message(user_message) else "english",
                team_id=team_id,
            )
            return reply
    except Exception as exc:
        logger.warning("DMR chatbot reply failed: %s", exc)
        await track_bot_reply(
            account_id, thread_id,
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
        await mark_disclosed(account_id, thread_id)
        return f"{disclosure_prefix}{fallback}"
    return fallback
