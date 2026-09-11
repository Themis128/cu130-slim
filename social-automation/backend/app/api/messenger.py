"""Facebook Messenger Platform API router.

Endpoints for setting up a Facebook Page as a Messenger channel,
configuring the Messenger Profile (greeting, Get Started, persistent
menu), sending messages, listing conversations, and receiving webhooks.

A Messenger "account" is a Facebook Page that has been subscribed to
the Messenger Platform.  The Page access token is stored in the
existing ``social_accounts`` table (platform="facebook",
account_type="page", meta_data.page_token).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
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
from app.services.messenger_api import (
    MessengerAPIClient,
    parse_webhook_event,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

async def _get_facebook_page_account(
    db: AsyncSession,
    account_id: uuid.UUID,
    user: User,
) -> SocialAccount:
    """Load a Facebook Page account and verify ownership."""
    result = await db.execute(
        select(SocialAccount).where(SocialAccount.id == account_id)
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if account.platform != "facebook" or account.account_type != "page":
        raise HTTPException(
            status_code=400,
            detail="Messenger setup requires a Facebook Page account",
        )
    # Verify team membership
    if account.team_id != user.team_id if hasattr(user, "team_id") else True:
        # Admin bypass
        if user.email != settings.SOCIAL_ADMIN_EMAIL and account.team_id != getattr(user, "team_id", None):
            raise HTTPException(status_code=403, detail="Not authorized to manage this account")
    return account


def _get_messenger_client(account: SocialAccount) -> MessengerAPIClient:
    """Build a MessengerAPIClient from a Facebook Page account."""
    meta = account.meta_data or {}
    page_token = meta.get("page_token")
    if not page_token:
        raise HTTPException(
            status_code=400,
            detail="No Page access token found. Reconnect the Facebook account.",
        )
    # page_token is stored encrypted in meta_data
    if isinstance(page_token, str) and not page_token.startswith("EA"):
        try:
            page_token = decrypt_token(page_token)
        except Exception:
            pass  # might be plaintext
    return MessengerAPIClient(
        access_token=page_token,
        page_id=account.account_id,
    )


# ------------------------------------------------------------------
# Pydantic schemas
# ------------------------------------------------------------------

class MessengerSetupRequest(BaseModel):
    greeting_text: str | None = Field(None, description="Custom greeting text. {page_name} is replaced.")
    enable_persistent_menu: bool = True
    enable_get_started: bool = True


class MessengerProfileResponse(BaseModel):
    account_id: uuid.UUID
    page_id: str
    page_name: str
    greeting: list[dict] | None = None
    get_started: dict | None = None
    persistent_menu: list[dict] | None = None
    whitelisted_domains: list[str] | None = None
    ice_breakers: list[dict] | None = None
    subscribed: bool = False


class MessengerProfileUpdate(BaseModel):
    greeting: list[dict] | None = None
    get_started: dict | None = None
    persistent_menu: list[dict] | None = None
    whitelisted_domains: list[str] | None = None
    ice_breakers: list[dict] | None = None


class SendMessageRequest(BaseModel):
    recipient_psid: str = Field(..., description="Page-Scoped ID of the recipient")
    text: str | None = None
    image_url: str | None = None
    messaging_type: str = "RESPONSE"


class SendQuickRepliesRequest(BaseModel):
    recipient_psid: str
    text: str
    quick_replies: list[dict]


class ConversationResponse(BaseModel):
    id: str
    snippet: str | None = None
    updated_time: str | None = None
    message_count: int | None = None
    unread_count: int | None = None
    participants: list[dict] | None = None


class MessageResponse(BaseModel):
    id: str
    message: str | None = None
    from_id: str | None = None
    created_time: str | None = None


class AutoReplyConfig(BaseModel):
    enabled: bool = False
    system_prompt: str = "You are a helpful assistant for {page_name}. Reply concisely and professionally."
    model: str = "@cf/meta/llama-3.1-8b-instruct"
    fallback_text: str = "Thanks for your message! We'll get back to you soon."
    max_tokens: int = 200


class WebhookVerifyRequest(BaseModel):
    hub_mode: str = Field("", alias="hub.mode")
    hub_verify_token: str = Field("", alias="hub.verify_token")
    hub_challenge: str = Field("", alias="hub.challenge")


# ------------------------------------------------------------------
# Setup & Profile endpoints
# ------------------------------------------------------------------

@router.post("/{account_id}/setup", response_model=dict)
async def setup_messenger(
    account_id: uuid.UUID,
    body: MessengerSetupRequest | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Set up a Facebook Page as a Messenger channel.

    1. Subscribes the Page to messaging webhooks
    2. Configures the Messenger Profile (greeting, Get Started, persistent menu)
    3. Stores the Messenger setup state in meta_data
    """
    account = await _get_facebook_page_account(db, account_id, user)
    client = _get_messenger_client(account)

    # Get Page info for greeting interpolation
    try:
        page_info = await client.get_page_info()
        page_name = page_info.get("name", account.display_name or "")
        page_url = page_info.get("website", "") or ""
        # Facebook Pages can store comma-separated URLs in the website field
        # (e.g. "https://cloudless.gr/, https://wa.me/...").  Extract the
        # first valid URL so the persistent menu link doesn't break.
        if page_url:
            page_url = page_url.split(",")[0].strip()
            if not page_url.startswith("http"):
                page_url = f"https://{page_url}"
    except Exception as e:
        logger.warning("Failed to get page info: %s", e)
        page_name = account.display_name or ""
        page_url = ""

    # 1. Subscribe Page to webhooks
    try:
        sub_result = await client.subscribe_page()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to subscribe Page: {e}")

    # 2. Set up Messenger Profile (rate limited: 10 calls per Page per 10 min)
    greeting_text = body.greeting_text if body else None
    try:
        profile_result = await client.setup_default_profile(
            page_name=page_name,
            page_url=page_url,
            greeting_text=greeting_text,
        )
    except Exception as e:
        err_msg = str(e)
        if "613" in err_msg or "rate limit" in err_msg.lower():
            # Profile API rate limited — subscription still succeeded.
            # Return partial success so the caller knows to retry profile later.
            profile_result = {
                "result": "rate_limited",
                "detail": "Messenger Profile API rate limit (10 calls/10 min). "
                          "Subscription succeeded; retry profile setup later.",
            }
            logger.warning("Messenger Profile rate limited for page_id=%s", account.account_id)
        else:
            raise HTTPException(status_code=502, detail=f"Failed to set Messenger Profile: {e}")

    # 3. Update account meta_data
    meta = account.meta_data or {}
    meta["messenger_setup"] = {
        "subscribed": True,
        "greeting_text": greeting_text,
        "persistent_menu": body.enable_persistent_menu if body else True,
        "get_started": body.enable_get_started if body else True,
    }
    account.meta_data = meta
    flag_modified(account, "meta_data")
    await db.commit()

    return {
        "status": "ok",
        "subscription": sub_result,
        "profile": profile_result,
        "page_name": page_name,
        "page_url": page_url,
    }


@router.get("/{account_id}/profile", response_model=MessengerProfileResponse)
async def get_messenger_profile(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get the current Messenger Profile for a Facebook Page."""
    account = await _get_facebook_page_account(db, account_id, user)
    client = _get_messenger_client(account)

    try:
        profile = await client.get_messenger_profile()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to get Messenger Profile: {e}")

    # Check subscription status
    subscribed = False
    try:
        apps = await client.get_subscribed_apps()
        subscribed = len(apps) > 0
    except Exception:
        pass

    page_info = {}
    try:
        page_info = await client.get_page_info()
    except Exception:
        pass

    return MessengerProfileResponse(
        account_id=account_id,
        page_id=account.account_id,
        page_name=page_info.get("name", account.display_name or ""),
        greeting=profile.get("greeting"),
        get_started=profile.get("get_started"),
        persistent_menu=profile.get("persistent_menu"),
        whitelisted_domains=profile.get("whitelisted_domains"),
        ice_breakers=profile.get("ice_breakers"),
        subscribed=subscribed,
    )


@router.put("/{account_id}/profile", response_model=dict)
async def update_messenger_profile(
    account_id: uuid.UUID,
    body: MessengerProfileUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Update one or more Messenger Profile properties."""
    account = await _get_facebook_page_account(db, account_id, user)
    client = _get_messenger_client(account)

    # Only include non-None fields
    profile: dict[str, Any] = {}
    if body.greeting is not None:
        profile["greeting"] = body.greeting
    if body.get_started is not None:
        profile["get_started"] = body.get_started
    if body.persistent_menu is not None:
        profile["persistent_menu"] = body.persistent_menu
    if body.whitelisted_domains is not None:
        profile["whitelisted_domains"] = body.whitelisted_domains
    if body.ice_breakers is not None:
        profile["ice_breakers"] = body.ice_breakers

    if not profile:
        raise HTTPException(status_code=400, detail="No profile properties to update")

    try:
        result = await client.set_messenger_profile(profile)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to update Messenger Profile: {e}")

    return result


@router.delete("/{account_id}/profile", response_model=dict)
async def delete_messenger_profile_fields(
    account_id: uuid.UUID,
    fields: list[str] = Query(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Delete specific Messenger Profile properties."""
    account = await _get_facebook_page_account(db, account_id, user)
    client = _get_messenger_client(account)

    try:
        result = await client.delete_messenger_profile_fields(fields)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to delete Messenger Profile fields: {e}")

    return result


@router.post("/{account_id}/unsubscribe", response_model=dict)
async def unsubscribe_messenger(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Remove the app's Messenger subscription from the Page."""
    account = await _get_facebook_page_account(db, account_id, user)
    client = _get_messenger_client(account)

    try:
        result = await client.unsubscribe_page()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to unsubscribe Page: {e}")

    # Update meta_data
    meta = account.meta_data or {}
    if "messenger_setup" in meta:
        meta["messenger_setup"]["subscribed"] = False
        account.meta_data = meta
        flag_modified(account, "meta_data")
        await db.commit()

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
    """Send a message to a person on Messenger."""
    account = await _get_facebook_page_account(db, account_id, user)
    client = _get_messenger_client(account)

    if not body.text and not body.image_url:
        raise HTTPException(status_code=400, detail="Either text or image_url is required")

    try:
        if body.text:
            result = await client.send_text(
                body.recipient_psid,
                body.text,
                messaging_type=body.messaging_type,
            )
        else:
            result = await client.send_image_url(body.recipient_psid, body.image_url)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to send message: {e}")

    return result


@router.post("/{account_id}/send-quick-replies", response_model=dict)
async def send_quick_replies(
    account_id: uuid.UUID,
    body: SendQuickRepliesRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Send a message with quick reply buttons."""
    account = await _get_facebook_page_account(db, account_id, user)
    client = _get_messenger_client(account)

    try:
        result = await client.send_quick_replies(
            body.recipient_psid,
            body.text,
            body.quick_replies,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to send quick replies: {e}")

    return result


# ------------------------------------------------------------------
# Conversations API
# ------------------------------------------------------------------

@router.get("/{account_id}/conversations", response_model=list[ConversationResponse])
async def list_conversations(
    account_id: uuid.UUID,
    limit: int = Query(25, ge=1, le=100),
    platform: str = Query("messenger"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List Messenger conversations for a Facebook Page."""
    account = await _get_facebook_page_account(db, account_id, user)
    client = _get_messenger_client(account)

    try:
        conversations = await client.get_conversations(platform=platform, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to list conversations: {e}")

    return [
        ConversationResponse(
            id=c.get("id", ""),
            snippet=c.get("snippet", ""),
            updated_time=c.get("updated_time", ""),
            message_count=c.get("message_count"),
            unread_count=c.get("unread_count"),
            participants=c.get("participants", {}).get("data", []) if isinstance(c.get("participants"), dict) else c.get("participants"),
        )
        for c in conversations
    ]


@router.get("/{account_id}/conversations/{conversation_id}", response_model=list[MessageResponse])
async def get_conversation_messages(
    account_id: uuid.UUID,
    conversation_id: str,
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get messages in a conversation thread."""
    account = await _get_facebook_page_account(db, account_id, user)
    client = _get_messenger_client(account)

    try:
        messages = await client.get_conversation_messages(conversation_id, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to get conversation messages: {e}")

    return [
        MessageResponse(
            id=m.get("id", ""),
            message=m.get("message", ""),
            from_id=m.get("from", {}).get("id") if isinstance(m.get("from"), dict) else None,
            created_time=m.get("created_time", ""),
        )
        for m in messages
    ]


# ------------------------------------------------------------------
# User Profile API
# ------------------------------------------------------------------

@router.get("/{account_id}/user/{psid}", response_model=dict)
async def get_user_profile(
    account_id: uuid.UUID,
    psid: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get the profile of a person who messaged the Page (by PSID)."""
    account = await _get_facebook_page_account(db, account_id, user)
    client = _get_messenger_client(account)

    try:
        profile = await client.get_user_profile(psid)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to get user profile: {e}")

    return profile


# ------------------------------------------------------------------
# Auto-reply configuration
# ------------------------------------------------------------------

@router.get("/{account_id}/auto-reply", response_model=AutoReplyConfig)
async def get_auto_reply_config(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get the AI auto-reply configuration for a Messenger account."""
    account = await _get_facebook_page_account(db, account_id, user)
    meta = account.meta_data or {}
    config = meta.get("messenger_auto_reply", {})
    return AutoReplyConfig(**config)


@router.put("/{account_id}/auto-reply", response_model=AutoReplyConfig)
async def update_auto_reply_config(
    account_id: uuid.UUID,
    body: AutoReplyConfig,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Update the AI auto-reply configuration for a Messenger account."""
    account = await _get_facebook_page_account(db, account_id, user)
    meta = account.meta_data or {}
    meta["messenger_auto_reply"] = body.model_dump()
    account.meta_data = meta
    flag_modified(account, "meta_data")
    await db.commit()
    return body


# ------------------------------------------------------------------
# Webhook endpoints
# ------------------------------------------------------------------

# The verify token is used to verify the webhook endpoint with Meta.
# It should be set in the MESSENGER_VERIFY_TOKEN env var.
def _get_verify_token() -> str:
    return settings.MESSENGER_VERIFY_TOKEN or "cloudless_messenger_verify"


def _verify_webhook_signature(raw_body: bytes, signature_header: str, app_secret: str) -> bool:
    """Verify the X-Hub-Signature-256 header using HMAC-SHA256.

    Meta signs every webhook POST body with HMAC-SHA256 keyed by the app
    secret.  The header format is ``sha256=<hex_digest>``.  We verify over
    the raw bytes before JSON parsing — Meta signs an escaped-unicode form
    of the payload, so a re-serialized JSON string will not match.
    """
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    sent_sig = signature_header.split("=", 1)[1].strip()
    expected_sig = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sent_sig, expected_sig)


@router.get("/webhook", response_model=str)
async def verify_webhook(
    hub_mode: str = Query("", alias="hub.mode"),
    hub_verify_token: str = Query("", alias="hub.verify_token"),
    hub_challenge: str = Query("", alias="hub.challenge"),
):
    """Verify the webhook endpoint with Meta.

    Meta sends a GET request with hub.mode=subscribe and a verify token.
    We must echo back the hub.challenge value.
    """
    if hub_mode == "subscribe" and hub_verify_token == _get_verify_token():
        return hub_challenge
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/webhook", response_model=dict)
async def receive_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Receive and process Messenger webhook events.

    This endpoint:
    1. Verifies the X-Hub-Signature-256 header (if app secret is configured)
    2. Parses incoming messages
    3. If auto-reply is enabled, generates an AI response and sends it
    4. Stores the conversation for the inbox UI
    """
    raw_body = await request.body()

    # Verify webhook signature if FACEBOOK_APP_SECRET is set
    app_secret = os.getenv("FACEBOOK_APP_SECRET", "")
    if app_secret:
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not _verify_webhook_signature(raw_body, signature, app_secret):
            raise HTTPException(status_code=403, detail="Invalid webhook signature")

    body = json.loads(raw_body)
    events = parse_webhook_event(body)

    processed = 0
    sidecar_url = os.getenv("MESSENGER_SIDECAR_URL", "")

    for event in events:
        page_id = event.get("page_id")
        sender_psid = event.get("sender_psid", "")
        message_text = event.get("message_text", "")
        message_type = event.get("message_type", "")
        message_mid = event.get("message_id", "")

        if message_type in ("text", "postback") and sender_psid:
            # Dispatch to sidecar for async processing (if configured)
            if sidecar_url:
                try:
                    import httpx
                    async with httpx.AsyncClient(timeout=5) as client:
                        await client.post(
                            f"{sidecar_url}/process",
                            json={
                                "page_id": page_id,
                                "sender_psid": sender_psid,
                                "recipient_id": page_id,
                                "message_text": message_text,
                                "message_type": message_type,
                                "message_mid": message_mid,
                                "timestamp": event.get("timestamp", 0),
                            },
                        )
                    processed += 1
                except Exception as e:
                    logger.warning("Sidecar dispatch failed, falling back to inline: %s", e)
                    # Fall back to inline processing
                    await _process_inline(db, page_id, sender_psid, message_text, message_type)
                    processed += 1
            else:
                # No sidecar — process inline
                await _process_inline(db, page_id, sender_psid, message_text, message_type)
                processed += 1

    return {"status": "ok", "events_received": len(events), "auto_replies_sent": processed}


async def _process_inline(
    db: AsyncSession,
    page_id: str,
    sender_psid: str,
    message_text: str,
    message_type: str,
) -> None:
    """Process a message inline (fallback when sidecar is unavailable)."""
    result = await db.execute(
        select(SocialAccount).where(
            SocialAccount.platform == "facebook",
            SocialAccount.account_id == page_id,
            SocialAccount.account_type == "page",
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        logger.warning("No Facebook Page account found for page_id=%s", page_id)
        return

    meta = account.meta_data or {}
    auto_reply = meta.get("messenger_auto_reply", {})
    if not auto_reply.get("enabled", False):
        return

    try:
        await _generate_and_send_auto_reply(account, sender_psid, message_text, auto_reply)
    except Exception as e:
        logger.error("Auto-reply failed for page_id=%s: %s", page_id, e)


async def _generate_and_send_auto_reply(
    account: SocialAccount,
    sender_psid: str,
    incoming_text: str,
    config: dict,
) -> None:
    """Generate an AI response and send it via Messenger."""
    from app.services.messenger_api import MessengerAPIClient

    # Build the Messenger client
    meta = account.meta_data or {}
    page_token = meta.get("page_token", "")
    if not page_token:
        return

    client = MessengerAPIClient(access_token=page_token, page_id=account.account_id)

    # Send typing indicator
    try:
        await client.send_sender_action(sender_psid, "typing_on")
    except Exception:
        pass

    # Generate AI response
    try:
        page_name = account.display_name or "Cloudless"
        system_prompt = config.get(
            "system_prompt",
            "You are a helpful assistant for {page_name}. Reply concisely and professionally.",
        ).replace("{page_name}", page_name)

        model = config.get("model", "@cf/meta/llama-3.1-8b-instruct")
        max_tokens = config.get("max_tokens", 200)
        fallback_text = config.get("fallback_text", "Thanks for your message! We'll get back to you soon.")

        # Use Cloudflare Workers AI (free, open-source-first)
        reply_text = await _generate_ai_response(
            system_prompt, incoming_text, model, max_tokens, fallback_text
        )
    except Exception as e:
        logger.error("AI response generation failed: %s", e)
        reply_text = config.get("fallback_text", "Thanks for your message! We'll get back to you soon.")

    # Send the reply
    await client.send_text(sender_psid, reply_text)

    # Stop typing indicator
    try:
        await client.send_sender_action(sender_psid, "typing_off")
    except Exception:
        pass


async def _generate_ai_response(
    system_prompt: str,
    user_message: str,
    model: str,
    max_tokens: int,
    fallback: str,
) -> str:
    """Generate an AI response using Cloudflare Workers AI (free-first).

    Falls back to DMR (local) if Cloudflare is unavailable.
    """
    import httpx

    # Try Cloudflare Workers AI first (free tier)
    cf_api_token = os.getenv("CLOUDFLARE_API_TOKEN", "")
    cf_account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")

    if cf_api_token and cf_account_id:
        try:
            url = f"https://api.cloudflare.com/client/v4/accounts/{cf_account_id}/ai/run/{model}"
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
            logger.warning("Cloudflare AI failed, trying DMR: %s", e)

    # Try DMR (local Docker Model Runner) as fallback
    dmr_url = os.getenv("DMR_BASE_URL", "http://localhost:12434")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{dmr_url}/engines/v1/chat/completions",
                json={
                    "model": "ai/qwen3:8b-q4_K_M",
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
        logger.warning("DMR AI failed: %s", e)

    return fallback


@router.get("/sidecar/status", response_model=dict)
async def sidecar_status() -> dict:
    """Check the Messenger webhook sidecar health and stats.

    Returns sidecar health, processing statistics, and configuration.
    If the sidecar is not configured or unreachable, returns status=offline.
    """
    sidecar_url = os.getenv("MESSENGER_SIDECAR_URL", "")
    if not sidecar_url:
        return {
            "status": "not_configured",
            "url": "",
            "message": "MESSENGER_SIDECAR_URL not set — webhook events processed inline",
        }

    import httpx

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            health_resp = await client.get(f"{sidecar_url}/health")
            stats_resp = await client.get(f"{sidecar_url}/stats")

        if health_resp.status_code == 200 and stats_resp.status_code == 200:
            return {
                "status": "online",
                "url": sidecar_url,
                "health": health_resp.json(),
                "stats": stats_resp.json(),
            }
        return {
            "status": "error",
            "url": sidecar_url,
            "message": f"Sidecar returned HTTP {health_resp.status_code}",
        }
    except Exception as e:
        return {
            "status": "offline",
            "url": sidecar_url,
            "message": f"Sidecar unreachable: {e}",
        }


# ------------------------------------------------------------------
# Personal Facebook Messenger (browser bridge)
# ------------------------------------------------------------------

def _get_browser_bridge_url() -> str:
    return os.getenv("BROWSER_BRIDGE_URL", "http://browser-novnc:9223")


async def _get_facebook_user_account(
    db: AsyncSession,
    account_id: uuid.UUID,
    user: User,
) -> SocialAccount:
    """Load a Facebook personal (user) account and verify ownership."""
    result = await db.execute(
        select(SocialAccount).where(SocialAccount.id == account_id)
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if account.platform != "facebook" or account.account_type != "user":
        raise HTTPException(
            status_code=400,
            detail="Personal Messenger requires a Facebook personal (user) account",
        )
    if user.email != settings.SOCIAL_ADMIN_EMAIL and account.team_id != getattr(user, "team_id", None):
        raise HTTPException(status_code=403, detail="Not authorized to manage this account")
    return account


class PersonalMessageSendRequest(BaseModel):
    thread_id: str
    text: str
    is_e2ee: bool = False


@router.get("/{account_id}/personal/conversations")
async def get_personal_conversations(
    account_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List personal Messenger conversations via the browser bridge.

    Navigates to facebook.com/messages and extracts the conversation list.
    Supports both regular and E2EE (end-to-end encrypted) threads.
    Requires a logged-in Facebook browser session (via noVNC).
    """
    _account = await _get_facebook_user_account(db, account_id, current_user)

    from app.services.browser_bridge import BrowserBridgeClient, BrowserBridgeError

    bridge = BrowserBridgeClient(_get_browser_bridge_url())

    # Check session and auto-restart if needed
    session = await bridge.ensure_session("facebook")
    if session["status"] != "active":
        raise HTTPException(
            status_code=503,
            detail=session["message"],
            headers={"X-Novnc-Url": session.get("novnc_url", "")},
        )

    try:
        result = await bridge.get_personal_messenger_conversations()
    except BrowserBridgeError as e:
        raise HTTPException(status_code=503, detail=f"Browser bridge error: {e.detail}")

    return result


@router.get("/{account_id}/personal/conversations/{thread_id}")
async def get_personal_messages(
    account_id: uuid.UUID,
    thread_id: str,
    is_e2ee: bool = Query(False, description="True for E2EE threads"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Read messages from a personal Messenger conversation thread.

    Supports both regular and E2EE threads.
    """
    _account = await _get_facebook_user_account(db, account_id, current_user)

    from app.services.browser_bridge import BrowserBridgeClient, BrowserBridgeError

    bridge = BrowserBridgeClient(_get_browser_bridge_url())

    # Check session and auto-restart if needed
    session = await bridge.ensure_session("facebook")
    if session["status"] != "active":
        raise HTTPException(
            status_code=503,
            detail=session["message"],
            headers={"X-Novnc-Url": session.get("novnc_url", "")},
        )

    try:
        result = await bridge.get_personal_messenger_messages(thread_id, is_e2ee=is_e2ee)
    except BrowserBridgeError as e:
        raise HTTPException(status_code=503, detail=f"Browser bridge error: {e.detail}")

    return result


@router.post("/{account_id}/personal/send")
async def send_personal_message(
    account_id: uuid.UUID,
    req: PersonalMessageSendRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Send a message in a personal Messenger conversation via the browser bridge.

    Supports both regular and E2EE threads.
    """
    _account = await _get_facebook_user_account(db, account_id, current_user)

    from app.services.browser_bridge import BrowserBridgeClient, BrowserBridgeError

    bridge = BrowserBridgeClient(_get_browser_bridge_url())

    # Check session and auto-restart if needed
    session = await bridge.ensure_session("facebook")
    if session["status"] != "active":
        raise HTTPException(
            status_code=503,
            detail=session["message"],
            headers={"X-Novnc-Url": session.get("novnc_url", "")},
        )

    try:
        result = await bridge.send_personal_messenger_message(req.thread_id, req.text, is_e2ee=req.is_e2ee)
    except BrowserBridgeError as e:
        raise HTTPException(status_code=503, detail=f"Browser bridge error: {e.detail}")

    return result


# ── Personal Messenger auto-reply config ────────────────────────────


class PersonalAutoReplyConfig(BaseModel):
    """Auto-reply configuration for personal Messenger (browser bridge)."""
    enabled: bool = False
    system_prompt: str = "You are a helpful assistant for {page_name}. Reply concisely and professionally."
    model: str = "ai/qwen3:8b-q4_K_M"
    fallback_text: str = "Thanks for your message! I'll get back to you soon."
    max_tokens: int = 300
    cooldown_seconds: int = 300
    temperature: float = 0.7


@router.get("/{account_id}/personal/auto-reply")
async def get_personal_auto_reply(
    account_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get personal Messenger AI auto-reply configuration."""
    account = await _get_facebook_user_account(db, account_id, current_user)
    meta = account.meta_data or {}
    config = meta.get("personal_messenger_auto_reply", {})
    return {
        "enabled": config.get("enabled", False),
        "system_prompt": config.get(
            "system_prompt",
            "You are a helpful assistant for {page_name}. Reply concisely and professionally.",
        ),
        "model": config.get("model", "ai/qwen3:8b-q4_K_M"),
        "fallback_text": config.get("fallback_text", "Thanks for your message! I'll get back to you soon."),
        "max_tokens": config.get("max_tokens", 300),
        "cooldown_seconds": config.get("cooldown_seconds", 300),
        "temperature": config.get("temperature", 0.7),
        "last_checked": meta.get("personal_messenger_last_checked"),
    }


@router.put("/{account_id}/personal/auto-reply")
async def update_personal_auto_reply(
    account_id: uuid.UUID,
    config: PersonalAutoReplyConfig,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update personal Messenger AI auto-reply configuration."""
    account = await _get_facebook_user_account(db, account_id, current_user)
    meta = account.meta_data or {}
    meta["personal_messenger_auto_reply"] = config.model_dump()
    account.meta_data = meta
    # Force SQLAlchemy to detect the JSONB mutation
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(account, "meta_data")
    await db.commit()
    return {"status": "ok", "config": config.model_dump()}


# ── Chatbot: per-conversation control ────────────────────────────────


class ThreadPauseRequest(BaseModel):
    thread_id: str
    reason: str = "human_handoff"


class ThreadConfigRequest(BaseModel):
    thread_id: str
    system_prompt: str | None = None
    model: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None


@router.post("/{account_id}/personal/threads/{thread_id}/pause")
async def pause_thread(
    account_id: uuid.UUID,
    thread_id: str,
    req: ThreadPauseRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Pause the chatbot for a specific conversation (human handoff)."""
    await _get_facebook_user_account(db, account_id, current_user)
    from app.services.messenger_chatbot import pause_thread as _pause
    await _pause(str(account_id), thread_id, req.reason)
    return {"status": "ok", "message": f"Thread {thread_id} paused"}


@router.post("/{account_id}/personal/threads/{thread_id}/resume")
async def resume_thread(
    account_id: uuid.UUID,
    thread_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Resume the chatbot for a specific conversation."""
    await _get_facebook_user_account(db, account_id, current_user)
    from app.services.messenger_chatbot import resume_thread as _resume
    await _resume(str(account_id), thread_id)
    return {"status": "ok", "message": f"Thread {thread_id} resumed"}


@router.get("/{account_id}/personal/threads/{thread_id}/config")
async def get_thread_config(
    account_id: uuid.UUID,
    thread_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get per-conversation chatbot config overrides."""
    await _get_facebook_user_account(db, account_id, current_user)
    from app.services.messenger_chatbot import get_thread_config as _get_config
    config = await _get_config(str(account_id), thread_id)
    return {"config": config}


@router.put("/{account_id}/personal/threads/{thread_id}/config")
async def set_thread_config(
    account_id: uuid.UUID,
    thread_id: str,
    req: ThreadConfigRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Set per-conversation chatbot config overrides."""
    await _get_facebook_user_account(db, account_id, current_user)
    from app.services.messenger_chatbot import set_thread_config as _set_config
    config = {k: v for k, v in req.model_dump().items() if v is not None and k != "thread_id"}
    await _set_config(str(account_id), thread_id, config)
    return {"status": "ok", "config": config}


@router.get("/{account_id}/personal/threads/{thread_id}/memory")
async def get_thread_memory(
    account_id: uuid.UUID,
    thread_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get conversation memory for a specific thread."""
    await _get_facebook_user_account(db, account_id, current_user)
    from app.services.messenger_chatbot import get_conversation_memory
    memory = await get_conversation_memory(str(account_id), thread_id)
    return {"messages": memory, "count": len(memory)}


@router.post("/{account_id}/personal/index-brand")
async def index_brand(
    account_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Index brand knowledge into ChromaDB for RAG-powered chatbot replies."""
    from app.models.brand import Brand
    from app.services.messenger_chatbot import index_brand_knowledge

    account = await _get_facebook_user_account(db, account_id, current_user)
    result = await db.execute(
        select(Brand).where(Brand.team_id == account.team_id)
    )
    brand = result.scalars().first()
    if not brand:
        return {"status": "error", "message": "No brand found for this team"}

    brand_data = {
        "name": brand.name,
        "tagline": getattr(brand, "tagline", None),
        "positioning_statement": getattr(brand, "positioning_statement", None),
        "mission": getattr(brand, "mission", None),
        "industry": getattr(brand, "industry", None),
        "values": getattr(brand, "values", []),
        "target_audience": getattr(brand, "target_audience", {}),
        "competitor_names": getattr(brand, "competitor_names", []),
    }
    indexed = await index_brand_knowledge(str(account.team_id), brand_data)
    return {"status": "ok", "indexed": indexed}


# ── Bot Builder: create and manage Messenger bots ───────────────────
#
# A "bot" in SocialAuto is a configurable auto-reply persona that can be
# created, customized, activated, and deactivated per account. Bots
# support:
#   - Custom personality (system prompt, tone, language)
#   - Intent-based responses (business, personal, spam, greeting, question)
#   - Brand knowledge RAG (ChromaDB)
#   - Conversation memory (last N messages)
#   - Per-conversation cooldown (Redis)
#   - Human handoff (pause/resume per thread)
#   - Quick-reply suggestions
#   - Business hours (only reply during configured hours)
#   - Auto-pause for specific contacts (e.g. family, VIP clients)
#
# For Page (business) accounts, the bot uses the Messenger Platform API
# (Graph API) for sending replies. For personal accounts, the bot uses
# the browser bridge.


class BotConfig(BaseModel):
    """Configuration for a Messenger bot."""
    name: str = Field("Cloudless Assistant", description="Bot display name")
    enabled: bool = Field(False, description="Whether the bot is active")
    system_prompt: str = Field(
        "You are a helpful assistant for {page_name}. Reply concisely and professionally "
        "in the same language as the incoming message.",
        description="System prompt for AI reply generation",
    )
    model: str = Field("ai/qwen3:8b-q4_K_M", description="AI model (DMR-first)")
    fallback_text: str = Field(
        "Thanks for your message! I will get back to you soon.",
        description="Fallback when AI is unavailable",
    )
    max_tokens: int = Field(300, description="Max tokens for AI reply")
    temperature: float = Field(0.7, description="AI temperature (0=deterministic, 1=creative)")
    cooldown_seconds: int = Field(300, description="Min seconds between replies per conversation")
    # Business hours (UTC). If set, bot only replies during these hours.
    business_hours_start: str | None = Field(None, description="UTC hour (e.g. '07:00') — bot only replies after this time")
    business_hours_end: str | None = Field(None, description="UTC hour (e.g. '22:00') — bot only replies before this time")
    # Auto-pause contacts (thread IDs that should never get auto-replies)
    paused_threads: list[str] = Field(default_factory=list, description="Thread IDs to skip (human handoff)")
    # Intent-based behavior
    reply_to_spam: bool = Field(False, description="Whether to auto-reply to spam")
    reply_to_greetings: bool = Field(True, description="Whether to auto-reply to greetings")
    # Quick replies (shown as suggestions in the inbox)
    quick_replies: list[dict] = Field(
        default_factory=lambda: [
            {"title": "Services", "payload": "BOT_SERVICES"},
            {"title": "Pricing", "payload": "BOT_PRICING"},
            {"title": "Contact", "payload": "BOT_CONTACT"},
        ],
        description="Quick-reply suggestions for the bot",
    )


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


# Personality presets
PERSONALITY_PRESETS = {
    "professional_friendly": (
        "You are {bot_name} for {page_name}. You are professional yet friendly. "
        "Reply in the same language as the incoming message (Greek or English). "
        "Be concise (2-3 sentences). For business inquiries, mention {page_name} services briefly. "
        "For personal messages, be warm. Never make up facts or prices — if unsure, say you will follow up."
    ),
    "casual": (
        "You are {bot_name} for {page_name}. You are casual, fun, and approachable. "
        "Reply in the same language as the incoming message. Keep it short and natural. "
        "Use emojis sparingly. Be yourself — like texting a friend."
    ),
    "formal": (
        "You are {bot_name} for {page_name}. You are formal, precise, and professional. "
        "Reply in the same language as the incoming message. Use complete sentences. "
        "Address the person by name if known. Be thorough but concise."
    ),
    "support": (
        "You are {bot_name}, a customer support assistant for {page_name}. "
        "Reply in the same language as the incoming message. Be helpful and patient. "
        "Ask clarifying questions if needed. For complex issues, offer to escalate to a human. "
        "Never make up information — if you don't know, say you'll check and follow up."
    ),
    "sales": (
        "You are {bot_name}, a sales assistant for {page_name}. "
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
    """Create a new Messenger bot for an account (personal or business).

    For Page accounts, this also:
    1. Subscribes the Page to webhooks (if not already)
    2. Sets up the Messenger Profile (greeting, Get Started, persistent menu)
    3. Configures the bot persona

    For personal accounts, this:
    1. Configures the auto-reply system prompt
    2. Indexes brand knowledge for RAG
    3. Enables the chatbot polling task
    """
    # Determine account type
    result = await db.execute(
        select(SocialAccount).where(SocialAccount.id == account_id)
    )
    account = result.scalars().first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if account.platform != "facebook":
        raise HTTPException(status_code=400, detail="Bot creation is only supported for Facebook accounts")

    is_page = account.account_type == "page"
    page_name = account.display_name or "Cloudless"

    # Build system prompt from personality preset
    if req.custom_prompt:
        system_prompt = req.custom_prompt.replace("{page_name}", page_name)
    else:
        preset = PERSONALITY_PRESETS.get(req.personality, PERSONALITY_PRESETS["professional_friendly"])
        system_prompt = preset.replace("{bot_name}", req.name).replace("{page_name}", page_name)

    # Build bot config
    bot_config = BotConfig(
        name=req.name,
        enabled=True,
        system_prompt=system_prompt,
        business_hours_start=req.business_hours_start,
        business_hours_end=req.business_hours_end,
    )

    # For Page accounts: set up Messenger Platform
    setup_result = None
    if is_page:
        try:
            client = _get_messenger_client(account)
            page_info = await client.get_page_info()
            page_name = page_info.get("name", page_name)
            page_url = page_info.get("website", "") or ""
            if page_url:
                page_url = page_url.split(",")[0].strip()
                if not page_url.startswith("http"):
                    page_url = f"https://{page_url}"

            # Subscribe Page to webhooks
            try:
                await client.subscribe_page()
            except Exception as e:
                logger.warning("Page subscription failed (may already be subscribed): %s", e)

            # Set up Messenger Profile with bot greeting
            greeting_text = f"Hi! 👋 I'm {req.name}. How can I help you today?"
            try:
                setup_result = await client.setup_default_profile(
                    page_name=page_name,
                    page_url=page_url,
                    greeting_text=greeting_text,
                )
            except Exception as e:
                logger.warning("Messenger Profile setup failed: %s", e)
                setup_result = {"result": "partial", "detail": str(e)}
        except Exception as e:
            logger.warning("Page setup failed: %s", e)
            setup_result = {"result": "error", "detail": str(e)}

    # Store bot config in account meta_data
    meta = account.meta_data or {}
    meta["messenger_bot"] = bot_config.model_dump()
    if is_page:
        meta["messenger_setup"] = {
            "subscribed": True,
            "bot_enabled": True,
            "bot_name": req.name,
        }
    else:
        # For personal accounts, also set auto-reply config
        meta["personal_messenger_auto_reply"] = {
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

    # Index brand knowledge for RAG
    brand_indexed = 0
    try:
        from app.models.brand import Brand
        from app.services.messenger_chatbot import index_brand_knowledge

        brand_result = await db.execute(
            select(Brand).where(Brand.team_id == account.team_id)
        )
        brand = brand_result.scalars().first()
        if brand:
            brand_data = {
                "name": brand.name,
                "tagline": getattr(brand, "tagline", None),
                "positioning_statement": getattr(brand, "positioning_statement", None),
                "mission": getattr(brand, "mission", None),
                "industry": getattr(brand, "industry", None),
                "values": getattr(brand, "values", []),
                "target_audience": getattr(brand, "target_audience", {}),
                "competitor_names": getattr(brand, "competitor_names", []),
            }
            brand_indexed = await index_brand_knowledge(str(account.team_id), brand_data)
    except Exception as e:
        logger.warning("Brand indexing failed: %s", e)

    return {
        "status": "ok",
        "bot": bot_config.model_dump(),
        "account_type": "page" if is_page else "user",
        "page_setup": setup_result,
        "brand_indexed": brand_indexed,
    }


@router.get("/{account_id}/bot")
async def get_bot(
    account_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the current bot configuration for an account."""
    result = await db.execute(
        select(SocialAccount).where(SocialAccount.id == account_id)
    )
    account = result.scalars().first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    meta = account.meta_data or {}
    bot_config = meta.get("messenger_bot")
    if not bot_config:
        return {"exists": False, "bot": None}

    # Check paused threads status from Redis
    paused_status = {}
    for thread_id in bot_config.get("paused_threads", []):
        try:
            from app.services.messenger_chatbot import is_thread_paused
            paused_status[thread_id] = await is_thread_paused(str(account_id), thread_id)
        except Exception:
            paused_status[thread_id] = False

    return {
        "exists": True,
        "bot": bot_config,
        "paused_threads_status": paused_status,
        "account_type": account.account_type,
    }


@router.put("/{account_id}/bot")
async def update_bot(
    account_id: uuid.UUID,
    config: BotConfig,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update the bot configuration for an account."""
    result = await db.execute(
        select(SocialAccount).where(SocialAccount.id == account_id)
    )
    account = result.scalars().first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    meta = account.meta_data or {}
    meta["messenger_bot"] = config.model_dump()

    # Sync auto-reply config for personal accounts
    if account.account_type == "user":
        meta["personal_messenger_auto_reply"] = {
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
    result = await db.execute(
        select(SocialAccount).where(SocialAccount.id == account_id)
    )
    account = result.scalars().first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    meta = account.meta_data or {}
    bot_config = meta.get("messenger_bot")
    if not bot_config:
        raise HTTPException(status_code=400, detail="No bot found — create one first")

    bot_config["enabled"] = True
    meta["messenger_bot"] = bot_config

    if account.account_type == "user":
        auto_reply = meta.get("personal_messenger_auto_reply", {})
        auto_reply["enabled"] = True
        meta["personal_messenger_auto_reply"] = auto_reply

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
    result = await db.execute(
        select(SocialAccount).where(SocialAccount.id == account_id)
    )
    account = result.scalars().first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    meta = account.meta_data or {}
    bot_config = meta.get("messenger_bot")
    if not bot_config:
        raise HTTPException(status_code=400, detail="No bot found")

    bot_config["enabled"] = False
    meta["messenger_bot"] = bot_config

    if account.account_type == "user":
        auto_reply = meta.get("personal_messenger_auto_reply", {})
        auto_reply["enabled"] = False
        meta["personal_messenger_auto_reply"] = auto_reply

    account.meta_data = meta
    flag_modified(account, "meta_data")
    await db.commit()
    return {"status": "ok", "enabled": False}


@router.post("/{account_id}/bot/pause-thread/{thread_id}")
async def bot_pause_thread(
    account_id: uuid.UUID,
    thread_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Pause the bot for a specific conversation (human handoff)."""
    result = await db.execute(
        select(SocialAccount).where(SocialAccount.id == account_id)
    )
    account = result.scalars().first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    meta = account.meta_data or {}
    bot_config = meta.get("messenger_bot", {})
    paused = bot_config.get("paused_threads", [])
    if thread_id not in paused:
        paused.append(thread_id)
        bot_config["paused_threads"] = paused
        meta["messenger_bot"] = bot_config
        account.meta_data = meta
        flag_modified(account, "meta_data")
        await db.commit()

    # Also set Redis pause for the polling task
    from app.services.messenger_chatbot import pause_thread as _pause
    await _pause(str(account_id), thread_id, "bot_paused")

    return {"status": "ok", "thread_id": thread_id, "paused": True}


@router.post("/{account_id}/bot/resume-thread/{thread_id}")
async def bot_resume_thread(
    account_id: uuid.UUID,
    thread_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Resume the bot for a specific conversation."""
    result = await db.execute(
        select(SocialAccount).where(SocialAccount.id == account_id)
    )
    account = result.scalars().first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    meta = account.meta_data or {}
    bot_config = meta.get("messenger_bot", {})
    paused = bot_config.get("paused_threads", [])
    if thread_id in paused:
        paused.remove(thread_id)
        bot_config["paused_threads"] = paused
        meta["messenger_bot"] = bot_config
        account.meta_data = meta
        flag_modified(account, "meta_data")
        await db.commit()

    from app.services.messenger_chatbot import resume_thread as _resume
    await _resume(str(account_id), thread_id)

    return {"status": "ok", "thread_id": thread_id, "paused": False}


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
