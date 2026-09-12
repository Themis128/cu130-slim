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
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.api.auth import get_current_user
from app.core.config import settings
from app.core.security import decrypt_token, encrypt_token
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


class WhatsAppCredentialsUpdate(BaseModel):
    """Update WhatsApp Cloud API credentials (Step 5 of Get Started).

    After creating a System User in Meta Business Settings and generating a
    permanent access token with whatsapp_business_messaging,
    whatsapp_business_management, and business_management permissions,
    store the token here so the app can send messages and receive webhooks.
    """
    access_token: str = Field(..., description="Permanent System User access token")
    phone_number_id: str = Field(..., description="WhatsApp Business phone number ID")
    waba_id: str | None = Field(None, description="WhatsApp Business Account ID")
    display_phone_number: str | None = Field(None, description="Display phone number (E.164)")
    subscribe_webhooks: bool = Field(True, description="Subscribe app to WABA webhooks after updating credentials")


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


# ── Phone number registration schemas (4-step flow) ──────────────────

class CreatePhoneNumberRequest(BaseModel):
    """Step 1: Create a business phone number on a WABA."""
    waba_id: str = Field(..., description="WhatsApp Business Account ID")
    cc: str = Field(..., description="Country calling code (e.g. '30' for Greece)")
    phone_number: str = Field(..., description="Phone number without country code")
    verified_name: str = Field(..., description="Display name for the business")


class RequestCodeRequest(BaseModel):
    """Step 2: Request a verification code."""
    phone_number_id: str = Field(..., description="Phone number ID from step 1")
    code_method: str = Field("SMS", description="Delivery method: 'SMS' or 'VOICE'")
    language: str = Field("en_US", description="Language code (e.g. 'en_US', 'el_GR')")


class VerifyCodeRequest(BaseModel):
    """Step 3: Verify the phone number with the code."""
    phone_number_id: str = Field(..., description="Phone number ID from step 1")
    code: str = Field(..., description="Verification code (with or without hyphen)")


class RegisterNumberRequest(BaseModel):
    """Step 4: Register the verified phone number for API use."""
    phone_number_id: str = Field(..., description="Verified phone number ID")
    pin: str = Field(..., description="6-digit two-step verification PIN")


class DeregisterNumberRequest(BaseModel):
    """Deregister a phone number (stops API use)."""
    phone_number_id: str = Field(..., description="Phone number ID to deregister")


class WabaSubscriptionRequest(BaseModel):
    """Subscribe or unsubscribe the app to webhooks on a WABA."""
    waba_id: str = Field(..., description="WhatsApp Business Account ID")


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


@router.put("/{account_id}/credentials", response_model=dict)
async def update_whatsapp_credentials(
    account_id: uuid.UUID,
    body: WhatsAppCredentialsUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Store permanent WhatsApp Cloud API credentials (Get Started Step 5).

    After creating a System User in Meta Business Settings and generating a
    permanent access token, call this endpoint to store the token, phone
    number ID, and WABA ID so the app can send messages and process webhooks.

    If ``subscribe_webhooks`` is true (default), also subscribes the app to
    webhooks on the WABA so incoming messages are delivered.
    """
    account = await _get_whatsapp_account(db, account_id, user)

    # Encrypt the access token before storing
    encrypted_token = encrypt_token(body.access_token)
    # encrypt_token returns bytes — decode for JSON storage in meta_data
    encrypted_token_str = encrypted_token.decode() if isinstance(encrypted_token, bytes) else encrypted_token

    meta = account.meta_data or {}
    meta["access_token"] = encrypted_token_str
    meta["phone_number_id"] = body.phone_number_id
    if body.waba_id:
        meta["waba_id"] = body.waba_id
    if body.display_phone_number:
        meta["display_phone_number"] = body.display_phone_number
    meta["credentials_configured"] = True
    account.meta_data = meta
    flag_modified(account, "meta_data")

    # Also update the encrypted token column for consistency
    account.access_token_enc = encrypted_token

    await db.commit()

    # Optionally subscribe to WABA webhooks
    webhook_result = None
    if body.subscribe_webhooks and body.waba_id:
        try:
            client = _get_whatsapp_client(account)
            webhook_result = await client.subscribe_app_to_waba(body.waba_id)
        except Exception as e:
            logger.warning(
                "WABA webhook subscription failed: %s", _sanitize_log_text(str(e))
            )
            webhook_result = {"success": False, "error": str(e)}

    return {
        "status": "ok",
        "credentials_stored": True,
        "phone_number_id": body.phone_number_id,
        "waba_id": body.waba_id,
        "webhook_subscription": webhook_result,
    }


@router.get("/{account_id}/setup-status", response_model=dict)
async def get_setup_status(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Check WhatsApp Cloud API setup status (Get Started checklist).

    Returns which Get Started steps are complete:
    - account_exists: SocialAuto WhatsApp account exists
    - credentials_configured: access token + phone_number_id stored
    - waba_id_configured: WABA ID is known
    - webhook_subscribed: app is subscribed to WABA webhooks
    - phone_number_registered: phone number is registered for API use
    - business_profile_set: business profile (about/description) configured
    - can_send_messages: credentials are present and phone is registered
    """
    account = await _get_whatsapp_account(db, account_id, user)
    meta = account.meta_data or {}

    access_token = meta.get("access_token", "")
    phone_number_id = meta.get("phone_number_id", "")
    waba_id = meta.get("waba_id", "")
    has_credentials = bool(access_token and phone_number_id)

    # Check live phone number status if credentials are present
    phone_info = None
    phone_registered = False
    webhook_subscribed = False
    if has_credentials:
        try:
            client = _get_whatsapp_client(account)
            phone_info = await client.get_phone_number_info()
            # code_verification_status = VERIFIED means registered
            phone_registered = phone_info.get("code_verification_status") == "VERIFIED"
        except Exception as e:
            logger.debug("Phone number info check failed: %s", _sanitize_log_text(str(e)))

        # Check webhook subscription
        if waba_id:
            try:
                client = _get_whatsapp_client(account)
                subs = await client.list_waba_subscriptions(waba_id)
                webhook_subscribed = len(subs) > 0
            except Exception as e:
                logger.debug("WABA subscription check failed: %s", _sanitize_log_text(str(e)))

    setup_data = meta.get("whatsapp_setup", {})
    profile_set = bool(setup_data.get("setup_complete"))

    return {
        "account_exists": True,
        "credentials_configured": has_credentials,
        "waba_id_configured": bool(waba_id),
        "webhook_subscribed": webhook_subscribed,
        "phone_number_registered": phone_registered,
        "business_profile_set": profile_set,
        "can_send_messages": has_credentials and phone_registered,
        "phone_number_info": phone_info,
        "display_phone_number": meta.get("display_phone_number", ""),
        "waba_id": waba_id,
        "phone_number_id": phone_number_id,
        "next_step": _next_setup_step(
            has_credentials, bool(waba_id), webhook_subscribed, phone_registered, profile_set
        ),
    }


def _next_setup_step(
    has_creds: bool, has_waba: bool, webhook_sub: bool, phone_reg: bool, profile_set: bool
) -> str:
    """Return the next incomplete Get Started step."""
    if not has_creds:
        return "Create a System User in Meta Business Settings and generate a permanent access token, then PUT /credentials"
    if not has_waba:
        return "Set the WABA ID via PUT /credentials (waba_id field)"
    if not phone_reg:
        return "Register the phone number via the 4-step registration flow (POST /register/*)"
    if not webhook_sub:
        return "Subscribe to WABA webhooks: POST /webhooks/subscribe"
    if not profile_set:
        return "Set up the business profile: POST /{account_id}/setup"
    return "setup_complete"


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
# Phone number registration (4-step flow)
# https://developers.facebook.com/docs/whatsapp/cloud-api/get-started/registering-phone-numbers
# ------------------------------------------------------------------

@router.post("/register/create-number", response_model=dict)
async def create_phone_number(
    body: CreatePhoneNumberRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Step 1: Create a business phone number on a WABA.

    Returns the new phone number ID (unverified). Use /register/request-code next.
    """
    # Use any WhatsApp account's token for the WABA-level call
    result = await db.execute(
        select(SocialAccount).where(SocialAccount.platform == "whatsapp").limit(1)
    )
    account = result.scalars().first()
    if not account:
        raise HTTPException(status_code=400, detail="No WhatsApp account found — connect one first")
    client = _get_whatsapp_client(account)

    try:
        result = await client.create_phone_number(
            waba_id=body.waba_id,
            cc=body.cc,
            phone_number=body.phone_number,
            verified_name=body.verified_name,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to create phone number: {e}")

    return result


@router.post("/register/request-code", response_model=dict)
async def request_verification_code(
    body: RequestCodeRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Step 2: Request a verification code sent via SMS or voice call.

    Meta sends a code like 'WhatsApp code 123-830' to the phone number.
    """
    result = await db.execute(
        select(SocialAccount).where(SocialAccount.platform == "whatsapp").limit(1)
    )
    account = result.scalars().first()
    if not account:
        raise HTTPException(status_code=400, detail="No WhatsApp account found")
    client = _get_whatsapp_client(account)

    try:
        result = await client.request_verification_code(
            phone_number_id=body.phone_number_id,
            code_method=body.code_method,
            language=body.language,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to request verification code: {e}")

    return result


@router.post("/register/verify-code", response_model=dict)
async def verify_phone_code(
    body: VerifyCodeRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Step 3: Verify the phone number with the code received via SMS/voice.

    The code should be numeric (hyphen stripped automatically, e.g. '123-830' → '123830').
    """
    result = await db.execute(
        select(SocialAccount).where(SocialAccount.platform == "whatsapp").limit(1)
    )
    account = result.scalars().first()
    if not account:
        raise HTTPException(status_code=400, detail="No WhatsApp account found")
    client = _get_whatsapp_client(account)

    try:
        result = await client.verify_code(
            phone_number_id=body.phone_number_id,
            code=body.code,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to verify code: {e}")

    return result


@router.post("/register/number", response_model=dict)
async def register_phone_number(
    body: RegisterNumberRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Step 4: Register the verified phone number for API use.

    Sets the 6-digit two-step verification PIN. After this, the number
    can send/receive messages via the Cloud API.
    """
    result = await db.execute(
        select(SocialAccount).where(SocialAccount.platform == "whatsapp").limit(1)
    )
    account = result.scalars().first()
    if not account:
        raise HTTPException(status_code=400, detail="No WhatsApp account found")
    client = _get_whatsapp_client(account)

    try:
        result = await client.register_number(
            phone_number_id=body.phone_number_id,
            pin=body.pin,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to register number: {e}")

    return result


@router.post("/register/deregister", response_model=dict)
async def deregister_phone_number(
    body: DeregisterNumberRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Deregister a business phone number (stops API use)."""
    result = await db.execute(
        select(SocialAccount).where(SocialAccount.platform == "whatsapp").limit(1)
    )
    account = result.scalars().first()
    if not account:
        raise HTTPException(status_code=400, detail="No WhatsApp account found")
    client = _get_whatsapp_client(account)

    try:
        result = await client.deregister_number(body.phone_number_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to deregister number: {e}")

    return result


# ------------------------------------------------------------------
# WABA webhook subscriptions (Subscribed Apps API)
# https://developers.facebook.com/docs/whatsapp/embedded-signup/webhooks
# ------------------------------------------------------------------

@router.post("/webhooks/subscribe", response_model=dict)
async def subscribe_app_to_waba(
    body: WabaSubscriptionRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Subscribe the app to webhooks on a WABA.

    POST /{waba_id}/subscribed_apps — subscribes your app to the WABA so
    Facebook sends webhook notifications to the app's callback URL for
    the subscribed fields (messages, account_update, etc.).

    The WABA ID comes from the Embedded Signup flow or the WhatsApp
    Business Account lookup. The callback URL is configured in the App
    Dashboard Webhooks panel.
    """
    result = await db.execute(
        select(SocialAccount).where(SocialAccount.platform == "whatsapp").limit(1)
    )
    account = result.scalars().first()
    if not account:
        raise HTTPException(status_code=400, detail="No WhatsApp account found")
    client = _get_whatsapp_client(account)

    try:
        return await client.subscribe_app_to_waba(body.waba_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to subscribe app to WABA: {e}")


@router.get("/webhooks/subscriptions/{waba_id}", response_model=list)
async def list_waba_subscriptions(
    waba_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List all apps subscribed to webhooks on a WABA.

    GET /{waba_id}/subscribed_apps — returns an array of apps with
    ``id``, ``link``, and ``name`` properties for each subscribed app.
    """
    result = await db.execute(
        select(SocialAccount).where(SocialAccount.platform == "whatsapp").limit(1)
    )
    account = result.scalars().first()
    if not account:
        raise HTTPException(status_code=400, detail="No WhatsApp account found")
    client = _get_whatsapp_client(account)

    try:
        return await client.list_waba_subscriptions(waba_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to list WABA subscriptions: {e}")


@router.delete("/webhooks/subscribe", response_model=dict)
async def unsubscribe_app_from_waba(
    body: WabaSubscriptionRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Unsubscribe the app from webhooks for a WABA.

    DELETE /{waba_id}/subscribed_apps — stops webhook notifications for
    this WABA from being sent to the app's callback URL.
    """
    result = await db.execute(
        select(SocialAccount).where(SocialAccount.platform == "whatsapp").limit(1)
    )
    account = result.scalars().first()
    if not account:
        raise HTTPException(status_code=400, detail="No WhatsApp account found")
    client = _get_whatsapp_client(account)

    try:
        return await client.unsubscribe_app_from_waba(body.waba_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to unsubscribe app from WABA: {e}")


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

    # Process WABA-level webhook fields (account_update, phone_number_*_update,
    # message_template_status_update, etc.) — these are not message events but
    # business/account notifications documented at:
    # https://developers.facebook.com/docs/whatsapp/embedded-signup/webhooks
    waba_fields_processed = _process_waba_level_events(body)

    # Process Flow response messages (interactive messages with nfm_context)
    flow_responses: list[dict] = []
    try:
        from app.api.whatsapp_flows import process_flow_responses
        flow_responses = await process_flow_responses(body, db)
    except Exception as e:
        logger.debug("Flow response parsing skipped: %s", e)

    processed = 0
    for event in events:
        phone_number_id = event.get("phone_number_id")
        sender_phone = event.get("sender_phone", "")
        sender_name = event.get("sender_name", "")
        message_text = event.get("message_text", "")
        message_type = event.get("message_type", "")
        message_id = event.get("message_id", "")

        if message_type in ("text", "button", "interactive") and sender_phone:
            await _process_inline(db, phone_number_id, sender_phone, sender_name, message_text, message_type, message_id)
            processed += 1
        elif message_type == "status":
            logger.debug("WhatsApp status: %s for message %s", event.get("status"), _sanitize_log_text(message_id))

    return {
        "status": "ok",
        "events_received": len(events),
        "auto_replies_sent": processed,
        "waba_fields_processed": waba_fields_processed,
        "flow_responses": len(flow_responses),
    }


def _process_waba_level_events(body: dict) -> int:
    """Process WABA-level webhook fields (non-message events).

    These events notify about changes to WABAs, phone numbers, message
    templates, account status, etc. They are logged and tracked but do
    not trigger auto-reply. See:
    https://developers.facebook.com/docs/whatsapp/embedded-signup/webhooks

    Supported fields:
    - account_update: WABA verification, ban, offboarding, reconnection
    - account_review_update: WABA policy review decision
    - account_alerts: phone number messaging limit / OBA status changes
    - business_capability_update: WABA capability changes (limits, etc.)
    - phone_number_name_update: display name verification outcome
    - phone_number_quality_update: phone number throughput level changes
    - message_template_status_update: template approval/rejection
    - message_template_quality_update: template quality score changes
    - message_template_components_update: template component changes
    - template_category_update: template category reclassification
    - security: phone number security setting changes
    """
    if body.get("object") != "whatsapp_business_account":
        return 0

    count = 0
    for entry in body.get("entry", []):
        waba_id = entry.get("id", "")
        for change in entry.get("changes", []):
            field = change.get("field", "")
            value = change.get("value", {})

            # Skip message events — those are handled by parse_webhook_event
            if field == "messages":
                continue

            count += 1
            if field == "account_update":
                event = value.get("event", "UNKNOWN")
                logger.info(
                    "WhatsApp WABA %s account_update: event=%s",
                    _sanitize_log_text(waba_id), event,
                )
                # Log ban info if present
                ban_info = value.get("ban_info")
                if ban_info:
                    logger.warning(
                        "WhatsApp WABA %s ban: state=%s date=%s",
                        _sanitize_log_text(waba_id),
                        ban_info.get("waba_ban_state"),
                        ban_info.get("waba_ban_date"),
                    )
            elif field == "account_review_update":
                decision = value.get("decision", "UNKNOWN")
                logger.info(
                    "WhatsApp WABA %s account_review_update: decision=%s",
                    _sanitize_log_text(waba_id), decision,
                )
            elif field == "account_alerts":
                logger.info(
                    "WhatsApp WABA %s account_alerts: %s",
                    _sanitize_log_text(waba_id),
                    _sanitize_log_text(json.dumps(value)),
                )
            elif field == "business_capability_update":
                logger.info(
                    "WhatsApp WABA %s business_capability_update: %s",
                    _sanitize_log_text(waba_id),
                    _sanitize_log_text(json.dumps(value)),
                )
            elif field == "phone_number_name_update":
                decision = value.get("decision", "UNKNOWN")
                name = value.get("requested_verified_name", "")
                logger.info(
                    "WhatsApp WABA %s phone_number_name_update: decision=%s name=%s",
                    _sanitize_log_text(waba_id), decision, _sanitize_log_text(name),
                )
            elif field == "phone_number_quality_update":
                event = value.get("event", "UNKNOWN")
                limit = value.get("current_limit", "")
                logger.info(
                    "WhatsApp WABA %s phone_number_quality_update: event=%s limit=%s",
                    _sanitize_log_text(waba_id), event, limit,
                )
            elif field in (
                "message_template_status_update",
                "message_template_quality_update",
                "message_template_components_update",
                "template_category_update",
            ):
                event = value.get("event", "UNKNOWN")
                template_name = value.get("message_template_name", "")
                logger.info(
                    "WhatsApp WABA %s %s: event=%s template=%s",
                    _sanitize_log_text(waba_id), field, event, _sanitize_log_text(template_name),
                )
            elif field == "security":
                logger.info(
                    "WhatsApp WABA %s security update: %s",
                    _sanitize_log_text(waba_id),
                    _sanitize_log_text(json.dumps(value)),
                )
            else:
                logger.info(
                    "WhatsApp WABA %s unhandled webhook field: %s",
                    _sanitize_log_text(waba_id), field,
                )

    return count


async def _process_inline(
    db: AsyncSession,
    phone_number_id: str,
    sender_phone: str,
    sender_name: str,
    message_text: str,
    message_type: str,
    message_id: str,
) -> None:
    """Process a message inline — full chatbot pipeline."""
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

    # Mark message as read
    try:
        client = _get_whatsapp_client(account)
        await client.mark_message_read(message_id)
    except Exception:
        pass

    # Run the full chatbot pipeline
    try:
        from app.services.whatsapp_chatbot import process_inbound_message

        bot_result = await process_inbound_message(
            account_id=str(account.id),
            team_id=str(account.team_id) if account.team_id else "",
            phone=sender_phone,
            sender_name=sender_name,
            message_text=message_text,
            message_id=message_id,
            config=auto_reply,
            account_name=account.display_name or "Cloudless",
        )

        if bot_result.get("skipped"):
            logger.info(
                "WhatsApp reply skipped for %s: %s",
                _sanitize_log_text(sender_phone),
                bot_result.get("reason", "unknown"),
            )
            return

        reply_text = bot_result.get("reply")
        if not reply_text:
            return

        # Send the reply via Cloud API (within the 24h customer service window)
        client = _get_whatsapp_client(account)
        await client.send_text(sender_phone, reply_text, messaging_type="RESPONSE")

    except Exception as e:
        logger.error(
            "WhatsApp auto-reply failed for phone_number_id=%s: %s",
            _sanitize_log_text(str(phone_number_id or "")),
            _sanitize_log_text(str(e)),
        )


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

    # Index brand knowledge for RAG
    brand_indexed = 0
    try:
        from app.models.brand import Brand
        from app.services.whatsapp_chatbot import index_brand_knowledge

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
        logger.warning("Brand indexing failed: %s", _sanitize_log_text(str(e)))

    return {
        "status": "ok",
        "bot": bot_config.model_dump(),
        "profile": profile_result,
        "brand_indexed": brand_indexed,
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


# ── Per-conversation control (human handoff) ─────────────────────────


class ThreadPauseRequest(BaseModel):
    phone: str = Field(..., description="Customer phone number to pause")
    reason: str = "human_handoff"


class ThreadConfigRequest(BaseModel):
    phone: str = Field(..., description="Customer phone number")
    system_prompt: str | None = None
    model: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None


@router.post("/{account_id}/threads/pause")
async def pause_thread(
    account_id: uuid.UUID,
    req: ThreadPauseRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Pause the bot for a specific phone number (human handoff)."""
    await _get_whatsapp_account(db, account_id, current_user)
    from app.services.whatsapp_chatbot import pause_thread as _pause
    await _pause(str(account_id), req.phone, req.reason)
    return {"status": "ok", "message": f"Thread for {req.phone} paused"}


@router.post("/{account_id}/threads/resume")
async def resume_thread(
    account_id: uuid.UUID,
    req: ThreadPauseRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Resume the bot for a specific phone number."""
    await _get_whatsapp_account(db, account_id, current_user)
    from app.services.whatsapp_chatbot import resume_thread as _resume
    await _resume(str(account_id), req.phone)
    return {"status": "ok", "message": f"Thread for {req.phone} resumed"}


@router.get("/{account_id}/threads/{phone}/config")
async def get_thread_config(
    account_id: uuid.UUID,
    phone: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get per-conversation config overrides."""
    await _get_whatsapp_account(db, account_id, current_user)
    from app.services.whatsapp_chatbot import get_thread_config as _get_config
    config = await _get_config(str(account_id), phone)
    return {"config": config}


@router.put("/{account_id}/threads/{phone}/config")
async def set_thread_config(
    account_id: uuid.UUID,
    phone: str,
    req: ThreadConfigRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Set per-conversation config overrides."""
    await _get_whatsapp_account(db, account_id, current_user)
    from app.services.whatsapp_chatbot import set_thread_config as _set_config
    config = {k: v for k, v in req.model_dump().items() if v is not None and k != "phone"}
    await _set_config(str(account_id), phone, config)
    return {"status": "ok", "config": config}


@router.get("/{account_id}/threads/{phone}/memory")
async def get_thread_memory(
    account_id: uuid.UUID,
    phone: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get conversation memory for a specific phone number."""
    await _get_whatsapp_account(db, account_id, current_user)
    from app.services.whatsapp_chatbot import get_conversation_memory
    memory = await get_conversation_memory(str(account_id), phone)
    return {"messages": memory, "count": len(memory)}


@router.get("/{account_id}/threads/{phone}/window")
async def get_service_window(
    account_id: uuid.UUID,
    phone: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Check the 24-hour customer service window status for a phone number."""
    await _get_whatsapp_account(db, account_id, current_user)
    from app.services.whatsapp_chatbot import get_window_remaining, is_in_service_window
    remaining = await get_window_remaining(str(account_id), phone)
    in_window = await is_in_service_window(str(account_id), phone)
    return {
        "phone": phone,
        "in_window": in_window,
        "seconds_remaining": remaining,
        "hours_remaining": round(remaining / 3600, 1) if remaining > 0 else 0,
    }


# ── Brand knowledge indexing ─────────────────────────────────────────


@router.post("/{account_id}/index-brand")
async def index_brand(
    account_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Index brand knowledge into ChromaDB for RAG-powered chatbot replies."""
    from app.models.brand import Brand
    from app.services.whatsapp_chatbot import index_brand_knowledge

    account = await _get_whatsapp_account(db, account_id, current_user)
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


# ── WhatsApp Phone Number Registration ─────────────────────────────────
# Meta's Cloud API requires phone numbers to be registered before they can
# send/receive messages. This is a 3-step process:
#   1. Request a verification code (SMS or voice)
#   2. Verify the code
#   3. Register the number for Cloud API use
# These endpoints automate steps 1-3 via the WhatsApp Cloud API.

@router.post("/{account_id}/phone/request-code")
async def request_phone_code(
    account_id: str,
    code_method: str = "SMS",
    language: str = "en_US",
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Request a verification code for the WhatsApp business phone number.

    The code is sent via SMS or voice call to the phone number.
    After receiving the code, call /phone/verify-code to verify it.
    """
    import httpx

    from app.core.security import decrypt_token

    account = await _get_whatsapp_account(db, account_id, current_user)
    if not account.access_token_enc:
        raise HTTPException(status_code=400, detail="Account has no access token")
    token = decrypt_token(account.access_token_enc)

    phone_number_id = (account.meta_data or {}).get("phone_number_id") or account.account_id or ""
    if not phone_number_id:
        raise HTTPException(status_code=400, detail="Account has no phone_number_id in meta_data")

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"https://graph.facebook.com/v21.0/{phone_number_id}/request_code",
            params={
                "code_method": code_method,
                "language": language,
                "access_token": token,
            },
        )

    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    return resp.json()


@router.post("/{account_id}/phone/verify-code")
async def verify_account_phone_code(
    account_id: str,
    code: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Verify the WhatsApp business phone number with the received code.

    After verification, the number is ready for Cloud API use.
    """
    import httpx

    from app.core.security import decrypt_token

    account = await _get_whatsapp_account(db, account_id, current_user)
    if not account.access_token_enc:
        raise HTTPException(status_code=400, detail="Account has no access token")
    token = decrypt_token(account.access_token_enc)

    phone_number_id = (account.meta_data or {}).get("phone_number_id") or account.account_id or ""
    if not phone_number_id:
        raise HTTPException(status_code=400, detail="Account has no phone_number_id in meta_data")

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"https://graph.facebook.com/v21.0/{phone_number_id}/verify_code",
            params={"code": code, "access_token": token},
        )

    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    return resp.json()


@router.post("/{account_id}/phone/register")
async def register_account_phone(
    account_id: str,
    pin: str = "",
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Register the verified WhatsApp business phone number for Cloud API use.

    This is the final step after phone number verification.
    The PIN is the 6-digit two-step verification PIN (if enabled).
    """
    import httpx

    from app.core.security import decrypt_token

    account = await _get_whatsapp_account(db, account_id, current_user)
    if not account.access_token_enc:
        raise HTTPException(status_code=400, detail="Account has no access token")
    token = decrypt_token(account.access_token_enc)

    phone_number_id = (account.meta_data or {}).get("phone_number_id") or account.account_id or ""
    if not phone_number_id:
        raise HTTPException(status_code=400, detail="Account has no phone_number_id in meta_data")

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"https://graph.facebook.com/v21.0/{phone_number_id}/register",
            json={
                "messaging_product": "whatsapp",
                "pin": pin,
            },
            params={"access_token": token},
        )

    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    # Update account metadata
    from sqlalchemy.orm.attributes import flag_modified
    meta = account.meta_data or {}
    meta["phone_registered"] = True
    meta["phone_registered_at"] = datetime.now(UTC).isoformat()
    account.meta_data = meta
    flag_modified(account, "meta_data")
    await db.commit()

    return resp.json()


@router.get("/{account_id}/phone/status")
async def get_phone_status(
    account_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Check the WhatsApp business phone number registration status."""
    import httpx

    from app.core.security import decrypt_token

    account = await _get_whatsapp_account(db, account_id, current_user)
    if not account.access_token_enc:
        raise HTTPException(status_code=400, detail="Account has no access token")
    token = decrypt_token(account.access_token_enc)

    phone_number_id = (account.meta_data or {}).get("phone_number_id") or account.account_id or ""
    if not phone_number_id:
        raise HTTPException(status_code=400, detail="Account has no phone_number_id in meta_data")

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"https://graph.facebook.com/v21.0/{phone_number_id}",
            params={
                "fields": "name,display_phone_number,quality_rating,code_verification_status",
                "access_token": token,
            },
        )

    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    return resp.json()
