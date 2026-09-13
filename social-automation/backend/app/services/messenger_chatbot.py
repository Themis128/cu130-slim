"""Personal Messenger chatbot service.

Provides conversation memory, brand knowledge RAG, intent detection,
per-conversation configuration, human handoff, and rate-limit-aware cooldowns
for the personal Messenger auto-reply system.

Services used (language-aware, free/open-source):
    - Docker Model Runner (Llama 3.2 / Qwen3 8B, local) — English reply generation + intent detection
    - Cloudflare Workers AI (Llama 3.1 8B) — Greek reply generation + fallback inference
    - ChromaDB (local) / Cloudflare Vectorize (cloud) — conversation memory + brand RAG
    - Redis — per-conversation cooldown + paused-thread tracking
    - PostgreSQL — per-conversation config + seen state
    - Brand DNA API — brand voice, pillars, positioning

Architecture:
    1. For each inbound message, detect intent (business, personal, spam, question, greeting)
    2. Retrieve conversation memory (last 10 messages from ChromaDB)
    3. Retrieve brand knowledge (RAG from ChromaDB/Vectorize)
    4. Build a context-aware prompt with brand voice + memory + intent
    5. Generate reply via language-aware routing:
       - Greek text → Cloudflare Workers AI (handles Greek correctly)
       - English/other → DMR (local, free, private)
       - Fallback: the other provider, then static text
    6. Check per-conversation cooldown (Redis, default 5 min)
    7. Check per-conversation pause (human handoff)
    8. Send reply and store in conversation memory
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

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
) -> str:
    """Generate a context-aware AI reply with conversation memory and brand knowledge.

    Inference fallback chain (language-aware per AGENTS.md):
    1. Greek text → Cloudflare Workers AI (Llama 3.1 8B) — handles Greek correctly
       Fallback: DMR (local) → static text
    2. English/other text → DMR (Llama 3.2, local, free) — primary
       Fallback: Cloudflare Workers AI → static text
    3. Final fallback: static text (with disclosure if first contact)

    Builds a rich system prompt that includes:
    - Base system prompt from config
    - Detected intent
    - Brand knowledge (RAG)
    - Conversation memory (last N messages)
    - Per-conversation overrides
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

    # Build enhanced system prompt
    enhanced_prompt = system_prompt

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
    memory = await get_conversation_memory(account_id, thread_id)
    if memory:
        memory_text = "\n".join(f"{'You' if m['sender'] == 'me' else 'Them'}: {m['text'][:100]}" for m in memory[-5:])
        enhanced_prompt += f"\n\nRecent conversation:\n{memory_text}"

    enhanced_prompt += "\n\nReply naturally in the same language as the user's message. Keep it short and conversational."

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
                                await mark_disclosed(account_id, thread_id)
                            return f"{disclosure_prefix}{text}" if not disclosed else text
        except Exception as exc:
            logger.warning("Cloudflare AI bot reply failed: %s", exc)

    # 2. Fallback: DMR (local, free, private)
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
                await mark_disclosed(account_id, thread_id)
            return f"{disclosure_prefix}{text}" if not disclosed else text
    except Exception as exc:
        logger.warning("DMR chatbot reply failed: %s", exc)

    # 3. Final fallback: static text (with disclosure if first contact)
    if not disclosed:
        await mark_disclosed(account_id, thread_id)
        return f"{disclosure_prefix}{fallback}"
    return fallback
