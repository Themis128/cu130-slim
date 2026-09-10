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


@router.get("/{account_id}/personal/conversations")
async def get_personal_conversations(
    account_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List personal Messenger conversations via the browser bridge.

    Navigates to facebook.com/messages and extracts the conversation list.
    Requires a logged-in Facebook browser session (via noVNC).
    """
    _account = await _get_facebook_user_account(db, account_id, current_user)

    from app.services.browser_bridge import BrowserBridgeClient, BrowserBridgeError

    bridge = BrowserBridgeClient(_get_browser_bridge_url())
    try:
        result = await bridge.get_personal_messenger_conversations()
    except BrowserBridgeError as e:
        raise HTTPException(status_code=503, detail=f"Browser bridge error: {e.detail}")

    return result


@router.get("/{account_id}/personal/conversations/{thread_id}")
async def get_personal_messages(
    account_id: uuid.UUID,
    thread_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Read messages from a personal Messenger conversation thread."""
    _account = await _get_facebook_user_account(db, account_id, current_user)

    from app.services.browser_bridge import BrowserBridgeClient, BrowserBridgeError

    bridge = BrowserBridgeClient(_get_browser_bridge_url())
    try:
        result = await bridge.get_personal_messenger_messages(thread_id)
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
    """Send a message in a personal Messenger conversation via the browser bridge."""
    _account = await _get_facebook_user_account(db, account_id, current_user)

    from app.services.browser_bridge import BrowserBridgeClient, BrowserBridgeError

    bridge = BrowserBridgeClient(_get_browser_bridge_url())
    try:
        result = await bridge.send_personal_messenger_message(req.thread_id, req.text)
    except BrowserBridgeError as e:
        raise HTTPException(status_code=503, detail=f"Browser bridge error: {e.detail}")

    return result


# ── Personal Messenger auto-reply config ────────────────────────────


class PersonalAutoReplyConfig(BaseModel):
    """Auto-reply configuration for personal Messenger (browser bridge)."""
    enabled: bool = False
    system_prompt: str = "You are a helpful assistant for {page_name}. Reply concisely and professionally."
    model: str = "@cf/meta/llama-3.1-8b-instruct"
    fallback_text: str = "Thanks for your message! I'll get back to you soon."
    max_tokens: int = 200


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
        "model": config.get("model", "@cf/meta/llama-3.1-8b-instruct"),
        "fallback_text": config.get("fallback_text", "Thanks for your message! I'll get back to you soon."),
        "max_tokens": config.get("max_tokens", 200),
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
    await db.commit()
    return {"status": "ok", "config": config.model_dump()}
