"""WhatsApp Business Cloud API router.

Endpoints for setting up a WhatsApp Business number, sending messages
(text, media, templates), receiving webhooks, and configuring an AI
auto-reply bot — mirroring the Messenger implementation.

A WhatsApp "account" is a WhatsApp Business phone number registered to the
Meta app.  The phone number ID and access token are stored in the existing
``social_accounts`` table (platform="whatsapp",
meta_data.phone_number_id, meta_data.access_token).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.api.auth import get_current_user
from app.core.config import settings
from app.core.security import decrypt_token
from app.db.session import get_db
from app.models.social_account import SocialAccount
from app.models.user import User
from app.services.facebook_api import _sanitize_log_text
from app.services.whatsapp_api import (
    WhatsAppAPIClient,
    parse_webhook_event,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

async def _get_whatsapp_account(
    db: AsyncSession,
    account_id: uuid.UUID,
    user: User,
) -> SocialAccount:
    """Load a WhatsApp account and verify ownership."""
    result = await db.execute(
        select(SocialAccount).where(SocialAccount.id == account_id)
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if account.platform != "whatsapp":
        raise HTTPException(
            status_code=400,
            detail="This endpoint requires a WhatsApp account",
        )
    if user.email != settings.SOCIAL_ADMIN_EMAIL and account.team_id != getattr(user, "team_id", None):
        raise HTTPException(status_code=403, detail="Not authorized to manage this account")
    return account


def _get_whatsapp_client(account: SocialAccount) -> WhatsAppAPIClient:
    """Build a WhatsAppAPIClient from a SocialAccount."""
    meta = account.meta_data or {}
    access_token = meta.get("access_token", "")
    phone_number_id = meta.get("phone_number_id", "")
    if not access_token or not phone_number_id:
        raise HTTPException(
            status_code=400,
            detail="No WhatsApp credentials found. Reconnect the WhatsApp account.",
        )
    # access_token is stored encrypted
    if isinstance(access_token, str) and not access_token.startswith("EA"):
        try:
            access_token = decrypt_token(access_token)
        except Exception:
            pass  # might be plaintext
    return WhatsAppAPIClient(
        access_token=access_token,
        phone_number_id=phone_number_id,
        business_phone=meta.get("display_phone_number", ""),
    )


# ------------------------------------------------------------------
# Pydantic schemas
# ------------------------------------------------------------------

class WhatsAppSetupRequest(BaseModel):
    greeting_text: str | None = Field(None, description="Custom greeting/about text for the business profile")


class WhatsAppProfileResponse(BaseModel):
    account_id: uuid.UUID
    phone_number_id: str
    display_phone_number: str | None = None
    verified_name: str | None = None
    about: str | None = None
    quality_rating: str | None = None
    profile_picture_url: str | None = None
    address: str | None = None
    description: str | None = None
    email: str | None = None
    websites: list[str] | None = None


class WhatsAppProfileUpdate(BaseModel):
    about: str | None = None
    address: str | None = None
    description: str | None = None
    email: str | None = None
    websites: list[str] | None = None
    vertical: str | None = None


class SendMessageRequest(BaseModel):
    to: str = Field(..., description="Recipient phone number (E.164, with or without +)")
    text: str | None = None
    image_url: str | None = None
    document_url: str | None = None
    filename: str | None = None
    caption: str | None = None
    preview_url: bool = False
    messaging_type: str = "RESPONSE"


class SendTemplateRequest(BaseModel):
    to: str = Field(..., description="Recipient phone number")
    template_name: str = Field(..., description="Approved template name")
    language_code: str = Field("en", description="Language code (en, el, etc.)")
    components: list[dict] | None = None


class AutoReplyConfig(BaseModel):
    enabled: bool = False
    system_prompt: str = "You are a helpful assistant for {business_name}. Reply concisely and professionally."
    model: str = "ai/qwen3:8b-q4_K_M"
    fallback_text: str = "Thanks for your message! We'll get back to you soon."
    max_tokens: int = 300
    cooldown_seconds: int = 300
    temperature: float = 0.7


# ------------------------------------------------------------------
# Setup & Profile endpoints
# ------------------------------------------------------------------

@router.post("/{account_id}/setup", response_model=dict)
async def setup_whatsapp(
    account_id: uuid.UUID,
    body: WhatsAppSetupRequest | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Set up a WhatsApp Business number.

    1. Verifies the phone number is connected
    2. Updates the business profile (about, description, websites)
    3. Stores the setup state in meta_data
    """
    account = await _get_whatsapp_account(db, account_id, user)
    client = _get_whatsapp_client(account)

    # Get phone number info
    try:
        phone_info = await client.get_phone_number_info()
        display_phone = phone_info.get("display_phone_number", "")
        verified_name = phone_info.get("verified_name", account.display_name or "")
    except Exception as e:
        logger.warning("Failed to get phone number info: %s", _sanitize_log_text(str(e)))
        display_phone = ""
        verified_name = account.display_name or ""

    # Update business profile if greeting/about text provided
    profile_result = None
    if body and body.greeting_text:
        try:
            profile_result = await client.update_business_profile({
                "about": body.greeting_text[:139],  # WhatsApp about limit
                "description": body.greeting_text[:512],
            })
        except Exception as e:
            logger.warning("Business profile update failed: %s", _sanitize_log_text(str(e)))
            profile_result = {"result": "error", "detail": str(e)}

    # Update account meta_data
    meta = account.meta_data or {}
    meta["whatsapp_setup"] = {
        "setup_complete": True,
        "display_phone_number": display_phone,
        "verified_name": verified_name,
        "greeting_text": body.greeting_text if body else None,
    }
    account.meta_data = meta
    flag_modified(account, "meta_data")
    await db.commit()

    return {
        "status": "ok",
        "phone_number_info": phone_info if "phone_info" in locals() else {},
        "profile": profile_result,
        "display_phone_number": display_phone,
        "verified_name": verified_name,
    }


@router.get("/{account_id}/profile", response_model=WhatsAppProfileResponse)
async def get_whatsapp_profile(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get the WhatsApp Business profile."""
    account = await _get_whatsapp_account(db, account_id, user)
    client = _get_whatsapp_client(account)

    try:
        profile = await client.get_business_profile()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to get WhatsApp profile: {e}")

    phone_info = {}
    try:
        phone_info = await client.get_phone_number_info()
    except Exception:
        pass

    return WhatsAppProfileResponse(
        account_id=account_id,
        phone_number_id=account.meta_data.get("phone_number_id", ""),
        display_phone_number=phone_info.get("display_phone_number", profile.get("display_phone_number")),
        verified_name=phone_info.get("verified_name", account.display_name),
        about=profile.get("about"),
        quality_rating=phone_info.get("quality_rating"),
        profile_picture_url=profile.get("profile_picture_url"),
        address=profile.get("address"),
        description=profile.get("description"),
        email=profile.get("email"),
        websites=profile.get("websites"),
    )


@router.put("/{account_id}/profile", response_model=dict)
async def update_whatsapp_profile(
    account_id: uuid.UUID,
    body: WhatsAppProfileUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Update WhatsApp Business profile fields."""
    account = await _get_whatsapp_account(db, account_id, user)
    client = _get_whatsapp_client(account)

    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No profile fields to update")

    try:
        result = await client.update_business_profile(updates)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to update WhatsApp profile: {e}")

    return result


# ------------------------------------------------------------------
# Send API
# ------------------------------------------------------------------

@router.post("/{account_id}/send", response_model=dict)
async def send_message(
    account_id: uuid.UUID,
    body: SendMessageRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Send a message to a WhatsApp number.

    Supports text, image (by URL), and document (by URL).
    For initiating a new conversation outside the 24-hour window, use /send-template instead.
    """
    account = await _get_whatsapp_account(db, account_id, user)
    client = _get_whatsapp_client(account)

    if not body.text and not body.image_url and not body.document_url:
        raise HTTPException(status_code=400, detail="Either text, image_url, or document_url is required")

    try:
        if body.text:
            result = await client.send_text(
                body.to, body.text,
                preview_url=body.preview_url,
                messaging_type=body.messaging_type,
            )
        elif body.image_url:
            result = await client.send_image(body.to, body.image_url, caption=body.caption)
        else:
            result = await client.send_document(body.to, body.document_url, filename=body.filename, caption=body.caption)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to send message: {e}")

    return result


@router.post("/{account_id}/send-template", response_model=dict)
async def send_template(
    account_id: uuid.UUID,
    body: SendTemplateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Send a template message (required for initiating conversations outside the 24-hour window)."""
    account = await _get_whatsapp_account(db, account_id, user)
    client = _get_whatsapp_client(account)

    try:
        result = await client.send_template(
            body.to, body.template_name,
            language_code=body.language_code,
            components=body.components,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to send template: {e}")

    return result


# ------------------------------------------------------------------
# Auto-reply configuration
# ------------------------------------------------------------------

@router.get("/{account_id}/auto-reply", response_model=AutoReplyConfig)
async def get_auto_reply_config(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get the AI auto-reply configuration for a WhatsApp account."""
    account = await _get_whatsapp_account(db, account_id, user)
    meta = account.meta_data or {}
    config = meta.get("whatsapp_auto_reply", {})
    return AutoReplyConfig(**config)


@router.put("/{account_id}/auto-reply", response_model=AutoReplyConfig)
async def update_auto_reply_config(
    account_id: uuid.UUID,
    body: AutoReplyConfig,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Update the AI auto-reply configuration for a WhatsApp account."""
    account = await _get_whatsapp_account(db, account_id, user)
    meta = account.meta_data or {}
    meta["whatsapp_auto_reply"] = body.model_dump()
    account.meta_data = meta
    flag_modified(account, "meta_data")
    await db.commit()
    return body


# ------------------------------------------------------------------
# Webhook endpoints
# ------------------------------------------------------------------

def _get_verify_token() -> str:
    return getattr(settings, "WHATSAPP_VERIFY_TOKEN", "") or "cloudless_whatsapp_verify"


def _verify_webhook_signature(raw_body: bytes, signature_header: str, app_secret: str) -> bool:
    """Verify the X-Hub-Signature-256 header using HMAC-SHA256.

    WhatsApp uses the same signing mechanism as Messenger Platform:
    HMAC-SHA256 keyed by the app secret, over the raw body bytes.
    """
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    sent_sig = signature_header.split("=", 1)[1].strip()
    expected_sig = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sent_sig, expected_sig)


@router.get("/webhook", response_class=PlainTextResponse)
async def verify_webhook(
    hub_mode: str = Query("", alias="hub.mode"),
    hub_verify_token: str = Query("", alias="hub.verify_token"),
    hub_challenge: str = Query("", alias="hub.challenge"),
):
    """Verify the webhook endpoint with Meta.

    Meta sends a GET request with hub.mode=subscribe and a verify token.
    We must echo back the hub.challenge value as plain text (not JSON).
    """
    if hub_mode == "subscribe" and hub_verify_token == _get_verify_token():
        return PlainTextResponse(hub_challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/webhook", response_model=dict)
async def receive_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Receive and process WhatsApp webhook events.

    This endpoint:
    1. Verifies the X-Hub-Signature-256 header (if app secret is configured)
    2. Parses incoming messages
    3. If auto-reply is enabled, generates an AI response and sends it
    4. Marks the message as read
    """
    raw_body = await request.body()

    # Verify webhook signature if FACEBOOK_APP_SECRET is set
    app_secret = settings.FACEBOOK_APP_SECRET or os.getenv("FACEBOOK_APP_SECRET", "")
    if app_secret:
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not _verify_webhook_signature(raw_body, signature, app_secret):
            raise HTTPException(status_code=403, detail="Invalid webhook signature")

    body = json.loads(raw_body)
    events = parse_webhook_event(body)

    processed = 0
    for event in events:
        phone_number_id = event.get("phone_number_id")
        sender_phone = event.get("sender_phone", "")
        message_text = event.get("message_text", "")
        message_type = event.get("message_type", "")
        message_id = event.get("message_id", "")

        if message_type in ("text", "button", "interactive") and sender_phone:
            await _process_inline(db, phone_number_id, sender_phone, message_text, message_type, message_id)
            processed += 1
        elif message_type == "status":
            logger.debug("WhatsApp status: %s for message %s", event.get("status"), _sanitize_log_text(message_id))

    return {"status": "ok", "events_received": len(events), "auto_replies_sent": processed}


async def _process_inline(
    db: AsyncSession,
    phone_number_id: str,
    sender_phone: str,
    message_text: str,
    message_type: str,
    message_id: str,
) -> None:
    """Process a message inline — generate AI reply and send it."""
    result = await db.execute(
        select(SocialAccount).where(
            SocialAccount.platform == "whatsapp",
            SocialAccount.meta_data["phone_number_id"].astext == phone_number_id,
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        logger.warning("No WhatsApp account found for phone_number_id=%s", _sanitize_log_text(str(phone_number_id or "")))
        return

    meta = account.meta_data or {}
    auto_reply = meta.get("whatsapp_auto_reply", {})
    if not auto_reply.get("enabled", False):
        return

    try:
        await _generate_and_send_auto_reply(account, sender_phone, message_text, auto_reply, message_id)
    except Exception as e:
        logger.error(
            "WhatsApp auto-reply failed for phone_number_id=%s: %s",
            _sanitize_log_text(str(phone_number_id or "")),
            _sanitize_log_text(str(e)),
        )


async def _generate_and_send_auto_reply(
    account: SocialAccount,
    sender_phone: str,
    incoming_text: str,
    config: dict,
    message_id: str,
) -> None:
    """Generate an AI response and send it via WhatsApp."""
    client = _get_whatsapp_client(account)

    # Mark message as read
    try:
        await client.mark_message_read(message_id)
    except Exception:
        pass

    # Generate AI response
    try:
        business_name = account.display_name or "Cloudless"
        system_prompt = config.get(
            "system_prompt",
            "You are a helpful assistant for {business_name}. Reply concisely and professionally.",
        ).replace("{business_name}", business_name)

        model = config.get("model", "ai/qwen3:8b-q4_K_M")
        max_tokens = config.get("max_tokens", 300)
        fallback_text = config.get("fallback_text", "Thanks for your message! We'll get back to you soon.")

        reply_text = await _generate_ai_response(
            system_prompt, incoming_text, model, max_tokens, fallback_text
        )
    except Exception as e:
        logger.error("AI response generation failed: %s", e)
        reply_text = config.get("fallback_text", "Thanks for your message! We'll get back to you soon.")

    # Send the reply (within the 24-hour customer service window)
    await client.send_text(sender_phone, reply_text, messaging_type="RESPONSE")


async def _generate_ai_response(
    system_prompt: str,
    user_message: str,
    model: str,
    max_tokens: int,
    fallback: str,
) -> str:
    """Generate an AI response using DMR (local, free-first) then Cloudflare Workers AI."""
    import httpx

    # Try DMR (local Docker Model Runner) first — free, private, no rate limits
    dmr_url = os.getenv("DMR_BASE_URL", "http://localhost:12434")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{dmr_url}/engines/v1/chat/completions",
                json={
                    "model": model if model.startswith("ai/") else "ai/qwen3:8b-q4_K_M",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    "max_tokens": max_tokens,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                choices = data.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", fallback).strip()
    except Exception as e:
        logger.warning("DMR AI failed, trying Cloudflare: %s", e)

    # Fallback: Cloudflare Workers AI (free tier)
    cf_api_token = os.getenv("CLOUDFLARE_API_TOKEN", "")
    cf_account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
    if cf_api_token and cf_account_id:
        try:
            cf_model = "@cf/meta/llama-3.1-8b-instruct"
            url = f"https://api.cloudflare.com/client/v4/accounts/{cf_account_id}/ai/run/{cf_model}"
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {cf_api_token}"},
                    json={
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_message},
                        ],
                        "max_tokens": max_tokens,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("result") and data["result"].get("response"):
                        return data["result"]["response"].strip()
        except Exception as e:
            logger.warning("Cloudflare AI failed: %s", e)

    return fallback


# ------------------------------------------------------------------
# Bot Builder (mirrors Messenger bot)
# ------------------------------------------------------------------

class BotConfig(BaseModel):
    """Configuration for a WhatsApp bot."""
    name: str = Field(default="Cloudless Assistant", description="Bot display name")
    enabled: bool = Field(default=False, description="Whether the bot is active")
    system_prompt: str = Field(
        default=(
            "You are a helpful assistant for {business_name}. Reply concisely and professionally "
            "in the same language as the incoming message."
        ),
        description="System prompt for AI reply generation",
    )
    model: str = Field(default="ai/qwen3:8b-q4_K_M", description="AI model (DMR-first)")
    fallback_text: str = Field(
        default="Thanks for your message! I will get back to you soon.",
        description="Fallback when AI is unavailable",
    )
    max_tokens: int = Field(default=300, description="Max tokens for AI reply")
    temperature: float = Field(default=0.7, description="AI temperature")
    cooldown_seconds: int = Field(default=300, description="Min seconds between replies per conversation")
    business_hours_start: str | None = Field(default=None, description="UTC hour (e.g. '07:00')")
    business_hours_end: str | None = Field(default=None, description="UTC hour (e.g. '22:00')")
    reply_to_spam: bool = Field(default=False, description="Whether to auto-reply to spam")
    reply_to_greetings: bool = Field(default=True, description="Whether to auto-reply to greetings")


class BotCreateRequest(BaseModel):
    """Request to create a new bot for an account."""
    name: str = Field("Cloudless Assistant", description="Bot display name")
    personality: str = Field(
        "professional_friendly",
        description="Bot personality preset: professional_friendly, casual, formal, support, sales",
    )
    language: str = Field("auto", description="Primary language: auto, en, el, or ISO code")
    business_hours_start: str | None = None
    business_hours_end: str | None = None
    custom_prompt: str | None = Field(None, description="Override the personality preset with a custom system prompt")


PERSONALITY_PRESETS = {
    "professional_friendly": (
        "You are {bot_name} for {business_name}. You are professional yet friendly. "
        "Reply in the same language as the incoming message (Greek or English). "
        "Be concise (2-3 sentences). For business inquiries, mention {business_name} services briefly. "
        "For personal messages, be warm. Never make up facts or prices — if unsure, say you will follow up."
    ),
    "casual": (
        "You are {bot_name} for {business_name}. You are casual, fun, and approachable. "
        "Reply in the same language as the incoming message. Keep it short and natural. "
        "Be yourself — like texting a friend."
    ),
    "formal": (
        "You are {bot_name} for {business_name}. You are formal, precise, and professional. "
        "Reply in the same language as the incoming message. Use complete sentences. "
        "Be thorough but concise."
    ),
    "support": (
        "You are {bot_name}, a customer support assistant for {business_name}. "
        "Reply in the same language as the incoming message. Be helpful and patient. "
        "Ask clarifying questions if needed. For complex issues, offer to escalate to a human. "
        "Never make up information — if you don't know, say you'll check and follow up."
    ),
    "sales": (
        "You are {bot_name}, a sales assistant for {business_name}. "
        "Reply in the same language as the incoming message. Be enthusiastic but not pushy. "
        "Highlight benefits, not features. Ask qualifying questions. "
        "Offer to schedule a call or share more info. Never make up prices."
    ),
}


@router.post("/{account_id}/bot/create")
async def create_bot(
    account_id: uuid.UUID,
    req: BotCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new WhatsApp bot for an account.

    1. Configures the bot persona with personality preset
    2. Sets up the auto-reply system
    3. Updates the business profile greeting
    """
    account = await _get_whatsapp_account(db, account_id, current_user)
    business_name = account.display_name or "Cloudless"

    # Build system prompt from personality preset
    if req.custom_prompt:
        system_prompt = req.custom_prompt.replace("{business_name}", business_name)
    else:
        preset = PERSONALITY_PRESETS.get(req.personality, PERSONALITY_PRESETS["professional_friendly"])
        system_prompt = preset.replace("{bot_name}", req.name).replace("{business_name}", business_name)

    # Apply language constraint
    if req.language and req.language != "auto":
        lang_names = {"en": "English", "el": "Greek (Ελληνικά)", "es": "Spanish", "de": "German", "fr": "French"}
        lang_name = lang_names.get(req.language, req.language)
        system_prompt += f" You must always reply in {lang_name}, regardless of the incoming message language."

    # Build bot config
    bot_config = BotConfig(
        name=req.name,
        enabled=True,
        system_prompt=system_prompt,
        business_hours_start=req.business_hours_start,
        business_hours_end=req.business_hours_end,
    )

    # Update business profile about text
    profile_result = None
    try:
        client = _get_whatsapp_client(account)
        about_text = f"🤖 {req.name} — AI Assistant"
        profile_result = await client.update_business_profile({"about": about_text[:139]})
    except Exception as e:
        logger.warning("WhatsApp profile update failed: %s", _sanitize_log_text(str(e)))
        profile_result = {"result": "error", "detail": str(e)}

    # Store bot config in account meta_data
    meta = account.meta_data or {}
    meta["whatsapp_bot"] = bot_config.model_dump()
    meta["whatsapp_auto_reply"] = {
        "enabled": True,
        "system_prompt": system_prompt,
        "model": bot_config.model,
        "fallback_text": bot_config.fallback_text,
        "max_tokens": bot_config.max_tokens,
        "cooldown_seconds": bot_config.cooldown_seconds,
        "temperature": bot_config.temperature,
    }
    account.meta_data = meta
    flag_modified(account, "meta_data")
    await db.commit()

    return {
        "status": "ok",
        "bot": bot_config.model_dump(),
        "profile": profile_result,
    }


@router.get("/{account_id}/bot")
async def get_bot(
    account_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the current bot configuration for an account."""
    account = await _get_whatsapp_account(db, account_id, current_user)
    meta = account.meta_data or {}
    bot_config = meta.get("whatsapp_bot")
    if not bot_config:
        return {"exists": False, "bot": None}
    return {"exists": True, "bot": bot_config}


@router.put("/{account_id}/bot")
async def update_bot(
    account_id: uuid.UUID,
    config: BotConfig,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update the bot configuration for an account."""
    account = await _get_whatsapp_account(db, account_id, current_user)
    meta = account.meta_data or {}
    meta["whatsapp_bot"] = config.model_dump()
    meta["whatsapp_auto_reply"] = {
        "enabled": config.enabled,
        "system_prompt": config.system_prompt,
        "model": config.model,
        "fallback_text": config.fallback_text,
        "max_tokens": config.max_tokens,
        "cooldown_seconds": config.cooldown_seconds,
        "temperature": config.temperature,
    }
    account.meta_data = meta
    flag_modified(account, "meta_data")
    await db.commit()
    return {"status": "ok", "bot": config.model_dump()}


@router.post("/{account_id}/bot/activate")
async def activate_bot(
    account_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Activate the bot for an account."""
    account = await _get_whatsapp_account(db, account_id, current_user)
    meta = account.meta_data or {}
    bot_config = meta.get("whatsapp_bot")
    if not bot_config:
        raise HTTPException(status_code=400, detail="No bot found — create one first")
    bot_config["enabled"] = True
    meta["whatsapp_bot"] = bot_config
    auto_reply = meta.get("whatsapp_auto_reply", {})
    auto_reply["enabled"] = True
    meta["whatsapp_auto_reply"] = auto_reply
    account.meta_data = meta
    flag_modified(account, "meta_data")
    await db.commit()
    return {"status": "ok", "enabled": True}


@router.post("/{account_id}/bot/deactivate")
async def deactivate_bot(
    account_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Deactivate the bot for an account (stops auto-replies)."""
    account = await _get_whatsapp_account(db, account_id, current_user)
    meta = account.meta_data or {}
    bot_config = meta.get("whatsapp_bot")
    if not bot_config:
        raise HTTPException(status_code=400, detail="No bot found")
    bot_config["enabled"] = False
    meta["whatsapp_bot"] = bot_config
    auto_reply = meta.get("whatsapp_auto_reply", {})
    auto_reply["enabled"] = False
    meta["whatsapp_auto_reply"] = auto_reply
    account.meta_data = meta
    flag_modified(account, "meta_data")
    await db.commit()
    return {"status": "ok", "enabled": False}


@router.get("/{account_id}/bot/personalities")
async def get_bot_personalities(
    account_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
):
    """List available bot personality presets."""
    return {
        "personalities": [
            {
                "id": key,
                "name": key.replace("_", " ").title(),
                "description": desc[:120] + "..." if len(desc) > 120 else desc,
            }
            for key, desc in PERSONALITY_PRESETS.items()
        ]
    }
