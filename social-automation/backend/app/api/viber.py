"""Viber Bot REST API router — credentials, webhook, send, broadcast, auto-reply.

Official docs: https://developers.viber.com/docs/api/rest-bot-api/

Accounts use ``platform="viber"`` with the bot auth token stored encrypted.
Inbound events arrive via ``POST /viber/webhook/{account_id}`` and are
authenticated with the ``X-Viber-Content-Signature`` header
(HMAC-SHA256 of the raw body keyed by the bot auth token).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.api.auth import get_current_user
from app.api.deps import TeamId, check_quota
from app.core.config import settings
from app.core.security import decrypt_token, encrypt_token
from app.db.session import get_db
from app.models.social_account import SocialAccount
from app.models.user import User
from app.services.viber_api import (
    VIBER_WEBHOOK_EVENTS,
    ViberAPIClient,
    ViberAPIError,
    parse_webhook_event,
    verify_signature,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _sanitize(text: str, max_len: int = 400) -> str:
    return (text or "").replace("\n", "\\n").replace("\r", "\\r")[:max_len]


async def _get_viber_account(
    db: AsyncSession,
    account_id: uuid.UUID,
    user: User,
) -> SocialAccount:
    result = await db.execute(select(SocialAccount).where(SocialAccount.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if account.platform != "viber":
        raise HTTPException(status_code=400, detail="This endpoint requires a Viber account")
    if user.email != settings.SOCIAL_ADMIN_EMAIL:
        from app.models.user import TeamMember

        mem = await db.execute(
            select(TeamMember).where(
                TeamMember.team_id == account.team_id,
                TeamMember.user_id == user.id,
            )
        )
        if mem.scalar_one_or_none() is None:
            raise HTTPException(status_code=403, detail="Not authorized to manage this account")
    return account


def _token_bytes(raw: str | bytes) -> bytes:
    if isinstance(raw, bytes):
        return raw
    return raw.encode()


def _decrypt_auth_token(account: SocialAccount) -> str:
    meta = account.meta_data or {}
    raw = meta.get("viber_auth_token_enc") or ""
    if raw:
        try:
            return decrypt_token(_token_bytes(raw))
        except Exception:
            pass
    if account.access_token_enc:
        try:
            return decrypt_token(_token_bytes(account.access_token_enc))
        except Exception:
            pass
    raise HTTPException(
        status_code=400,
        detail="No Viber auth token stored. Add credentials first.",
    )


def _client_for(account: SocialAccount) -> ViberAPIClient:
    return ViberAPIClient(_decrypt_auth_token(account))


def _webhook_base() -> str:
    base = (getattr(settings, "VIBER_WEBHOOK_BASE", None) or "").rstrip("/")
    if base:
        return base
    media = (getattr(settings, "MEDIA_PUBLIC_BASE_URL", None) or "").rstrip("/")
    if media.startswith("https://"):
        from urllib.parse import urlparse

        parsed = urlparse(media)
        return f"{parsed.scheme}://{parsed.netloc}/api/v1"
    return "https://social.cloudless.gr/api/v1"


def _webhook_url_for(account_id: uuid.UUID) -> str:
    return f"{_webhook_base()}/viber/webhook/{account_id}"


# ── Schemas ──────────────────────────────────────────────────────────


class ViberConnectRequest(BaseModel):
    auth_token: str = Field(..., description="Viber bot auth token from partners.viber.com")
    set_webhook: bool = Field(True, description="Register HTTPS webhook after connect")


class ViberCredentialsUpdate(BaseModel):
    auth_token: str = Field(..., description="Viber bot auth token")
    set_webhook: bool = Field(False, description="Also register webhook after update")


class SendMessageRequest(BaseModel):
    receiver: str = Field(..., description="Viber user id (must be subscribed to the bot)")
    text: str = Field(..., min_length=1, max_length=7000)


class SendPictureRequest(BaseModel):
    receiver: str
    media_url: str = Field(..., description="Public HTTPS URL of the image")
    text: str = ""


class BroadcastRequest(BaseModel):
    broadcast_list: list[str] = Field(
        ..., max_length=300, description="Subscribed Viber user ids (max 300)"
    )
    text: str = Field(..., min_length=1, max_length=7000)


class AutoReplyConfig(BaseModel):
    enabled: bool = False
    system_prompt: str = (
        "You are a helpful assistant for {business_name}. Reply concisely and professionally "
        "in the same language as the incoming message."
    )
    model: str = "ai/qwen3:8b-q4_K_M"
    fallback_text: str = "Thanks for your message! I will get back to you soon."
    max_tokens: int = 300
    temperature: float = 0.7
    cooldown_seconds: int = 300
    reply_to_spam: bool = False
    reply_to_greetings: bool = True
    welcome_message: str = ""


# ── Connect / credentials ────────────────────────────────────────────


@router.post("/connect", response_model=dict)
async def connect_viber_bot(
    body: ViberConnectRequest,
    team_id: TeamId,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Create a Viber bot account from its auth token."""
    await check_quota("social_accounts", team_id, db)
    try:
        client = ViberAPIClient(body.auth_token)
        info = await client.get_account_info()
    except (ViberAPIError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid auth token: {exc}") from exc

    bot_id = str(info.get("id") or "")
    name = info.get("name") or "Viber Bot"
    uri = info.get("uri") or ""
    if not bot_id:
        raise HTTPException(status_code=400, detail="get_account_info did not return a bot id")

    existing = await db.execute(
        select(SocialAccount).where(
            SocialAccount.team_id == team_id,
            SocialAccount.platform == "viber",
            SocialAccount.account_id == bot_id,
        )
    )
    account = existing.scalar_one_or_none()
    enc = encrypt_token(body.auth_token)
    meta: dict[str, Any] = {
        "viber_auth_token_enc": enc.decode() if isinstance(enc, bytes) else enc,
        "bot_id": bot_id,
        "bot_uri": uri,
        "credentials_configured": True,
        "subscribers_count": info.get("subscribers_count"),
        "viber_webhook_events": info.get("event_types"),
    }

    if account:
        account.access_token_enc = enc if isinstance(enc, bytes) else enc.encode()
        account.username = uri or account.username
        account.display_name = name
        account.status = "active"
        account.meta_data = {**(account.meta_data or {}), **meta}
        flag_modified(account, "meta_data")
    else:
        account = SocialAccount(
            team_id=team_id,
            platform="viber",
            account_id=bot_id,
            username=uri or None,
            display_name=name,
            account_type="bot",
            is_business=True,
            access_token_enc=enc if isinstance(enc, bytes) else enc.encode(),
            scopes=["bot"],
            status="active",
            meta_data=meta,
        )
        db.add(account)

    await db.flush()

    webhook_result = None
    if body.set_webhook:
        webhook_result = await _register_webhook(account, client)

    await db.commit()
    await db.refresh(account)

    return {
        "status": "ok",
        "account_id": str(account.id),
        "bot_id": bot_id,
        "bot_name": name,
        "bot_uri": uri,
        "subscribers_count": info.get("subscribers_count"),
        "webhook": webhook_result,
    }


@router.put("/{account_id}/credentials", response_model=dict)
async def update_viber_credentials(
    account_id: uuid.UUID,
    body: ViberCredentialsUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    account = await _get_viber_account(db, account_id, user)
    try:
        client = ViberAPIClient(body.auth_token)
        info = await client.get_account_info()
    except (ViberAPIError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid auth token: {exc}") from exc

    bot_id = str(info.get("id") or "")
    enc = encrypt_token(body.auth_token)
    meta = dict(account.meta_data or {})
    meta.update(
        {
            "viber_auth_token_enc": enc.decode() if isinstance(enc, bytes) else enc,
            "bot_id": bot_id,
            "bot_uri": info.get("uri") or "",
            "credentials_configured": True,
        }
    )

    account.access_token_enc = enc if isinstance(enc, bytes) else enc.encode()
    account.account_id = bot_id or account.account_id
    account.username = info.get("uri") or account.username
    account.display_name = info.get("name") or account.display_name
    account.status = "active"
    account.meta_data = meta
    flag_modified(account, "meta_data")

    webhook_result = None
    if body.set_webhook:
        webhook_result = await _register_webhook(account, client)

    await db.commit()
    return {
        "status": "ok",
        "credentials_stored": True,
        "bot_id": bot_id,
        "bot_uri": info.get("uri"),
        "webhook": webhook_result,
    }


async def _register_webhook(account: SocialAccount, client: ViberAPIClient) -> dict[str, Any]:
    meta = dict(account.meta_data or {})
    url = _webhook_url_for(account.id)
    try:
        events = await client.set_webhook(url, event_types=list(VIBER_WEBHOOK_EVENTS))
        meta["webhook_url"] = url
        meta["webhook_set"] = True
        meta["viber_webhook_events"] = events
        account.meta_data = meta
        flag_modified(account, "meta_data")
        return {"ok": True, "url": url, "event_types": events}
    except (ViberAPIError, ValueError) as exc:
        meta["webhook_set"] = False
        meta["webhook_error"] = str(exc)
        account.meta_data = meta
        flag_modified(account, "meta_data")
        return {"ok": False, "url": url, "error": str(exc)}


@router.post("/{account_id}/setup-webhook", response_model=dict)
async def setup_viber_webhook(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Call official set_webhook for this bot account."""
    account = await _get_viber_account(db, account_id, user)
    client = _client_for(account)
    result = await _register_webhook(account, client)
    await db.commit()
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error") or "set_webhook failed")
    return {"status": "ok", **result}


@router.post("/{account_id}/delete-webhook", response_model=dict)
async def delete_viber_webhook(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    account = await _get_viber_account(db, account_id, user)
    client = _client_for(account)
    try:
        await client.unset_webhook()
    except ViberAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    meta = dict(account.meta_data or {})
    meta["webhook_set"] = False
    meta.pop("webhook_url", None)
    account.meta_data = meta
    flag_modified(account, "meta_data")
    await db.commit()
    return {"status": "ok", "deleted": True}


@router.get("/{account_id}/setup-status", response_model=dict)
async def get_setup_status(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    account = await _get_viber_account(db, account_id, user)
    meta = account.meta_data or {}
    has_creds = bool(meta.get("credentials_configured") or meta.get("viber_auth_token_enc"))
    info = None
    token_valid = False
    if has_creds:
        try:
            client = _client_for(account)
            info = await client.get_account_info()
            token_valid = True
        except Exception as exc:
            logger.debug("Viber setup-status live check failed: %s", _sanitize(str(exc)))

    live_url = (info or {}).get("webhook") or ""
    webhook_ok = bool(live_url) and live_url.startswith("https://")

    next_step = "setup_complete"
    if not has_creds:
        next_step = (
            "Create a Viber bot via a verified partner "
            "(partners.viber.com), then paste the auth token via Connect / credentials"
        )
    elif not token_valid:
        next_step = "Auth token is invalid — update credentials"
    elif not webhook_ok:
        next_step = (
            "Call Setup Webhook so Viber can POST events to SocialAuto "
            "(requires public HTTPS, e.g. social.cloudless.gr)"
        )
    elif not (meta.get("viber_auto_reply") or {}).get("enabled"):
        next_step = "Enable auto-reply for the bot"

    return {
        "account_exists": True,
        "credentials_configured": has_creds,
        "token_valid": token_valid,
        "webhook_set": webhook_ok or bool(meta.get("webhook_set")),
        "webhook_url": live_url or meta.get("webhook_url") or _webhook_url_for(account.id),
        "bot": info,
        "bot_uri": meta.get("bot_uri") or account.username,
        "bot_id": meta.get("bot_id") or account.account_id,
        "subscribers_count": (info or {}).get("subscribers_count"),
        "can_send_messages": token_valid,
        "next_step": next_step,
        "note": (
            "Bots can only message subscribed users. Broadcast requires Viber "
            "account-manager approval (status 15 until granted)."
        ),
    }


# ── Send / broadcast ─────────────────────────────────────────────────


@router.post("/{account_id}/send", response_model=dict)
async def send_message(
    account_id: uuid.UUID,
    body: SendMessageRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    account = await _get_viber_account(db, account_id, user)
    client = _client_for(account)
    sender = (account.meta_data or {}).get("bot_name") or account.display_name or "Cloudless"
    try:
        result = await client.send_text(body.receiver, body.text, sender_name=sender)
    except (ViberAPIError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"status": "ok", "message_token": result.get("message_token")}


@router.post("/{account_id}/send-picture", response_model=dict)
async def send_picture(
    account_id: uuid.UUID,
    body: SendPictureRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    account = await _get_viber_account(db, account_id, user)
    client = _client_for(account)
    sender = (account.meta_data or {}).get("bot_name") or account.display_name or "Cloudless"
    try:
        result = await client.send_picture(
            body.receiver, body.media_url, text=body.text, sender_name=sender
        )
    except (ViberAPIError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"status": "ok", "message_token": result.get("message_token")}


@router.post("/{account_id}/broadcast", response_model=dict)
async def broadcast(
    account_id: uuid.UUID,
    body: BroadcastRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Broadcast to subscribed users — requires Viber approval; status 15
    (publicAccountNotAuthorized) is surfaced as a clear 502 until granted."""
    account = await _get_viber_account(db, account_id, user)
    client = _client_for(account)
    sender = (account.meta_data or {}).get("bot_name") or account.display_name or "Cloudless"
    try:
        result = await client.broadcast_text(
            body.broadcast_list, body.text, sender_name=sender
        )
    except (ViberAPIError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"status": "ok", "result": result}


# ── Auto-reply ───────────────────────────────────────────────────────


@router.get("/{account_id}/auto-reply", response_model=AutoReplyConfig)
async def get_auto_reply_config(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    account = await _get_viber_account(db, account_id, user)
    config = (account.meta_data or {}).get("viber_auto_reply", {})
    return AutoReplyConfig(**config)


@router.put("/{account_id}/auto-reply", response_model=AutoReplyConfig)
async def update_auto_reply_config(
    account_id: uuid.UUID,
    body: AutoReplyConfig,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    account = await _get_viber_account(db, account_id, user)
    if body.enabled:
        from app.api.deps import check_plan_feature

        await check_plan_feature("dm_auto_reply", account.team_id, db)
    meta = dict(account.meta_data or {})
    meta["viber_auto_reply"] = body.model_dump()
    account.meta_data = meta
    flag_modified(account, "meta_data")
    await db.commit()
    return body


@router.post("/{account_id}/threads/{user_id}/pause", response_model=dict)
async def pause_thread(
    account_id: uuid.UUID,
    user_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _get_viber_account(db, account_id, user)
    from app.services.viber_chatbot import pause_thread as _pause

    await _pause(str(account_id), user_id)
    return {"status": "ok", "paused": True}


@router.post("/{account_id}/threads/{user_id}/resume", response_model=dict)
async def resume_thread(
    account_id: uuid.UUID,
    user_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _get_viber_account(db, account_id, user)
    from app.services.viber_chatbot import resume_thread as _resume

    await _resume(str(account_id), user_id)
    return {"status": "ok", "paused": False}


# ── Webhook ──────────────────────────────────────────────────────────


@router.post("/webhook/{account_id}", response_model=dict)
async def receive_webhook(
    account_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_viber_content_signature: str | None = Header(
        None, alias="X-Viber-Content-Signature"
    ),
):
    """Receive Viber webhook callbacks (set_webhook push)."""
    result = await db.execute(
        select(SocialAccount).where(
            SocialAccount.id == account_id,
            SocialAccount.platform == "viber",
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Unknown Viber account")

    raw_body = await request.body()
    try:
        auth_token = _decrypt_auth_token(account)
    except HTTPException as exc:
        raise HTTPException(status_code=400, detail="Viber credentials missing") from exc

    if not verify_signature(raw_body, x_viber_content_signature or "", auth_token):
        raise HTTPException(status_code=403, detail="Invalid webhook signature")

    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    if not isinstance(payload, dict):
        return {"status": "ok", "ignored": True}

    event = parse_webhook_event(payload)
    event_name = event.get("event")
    response: dict[str, Any] = {"status": "ok", "event": event_name}

    # Registration ping Viber sends when set_webhook is called.
    if event_name == "webhook":
        return response

    if event_name in ("subscribed", "unsubscribed", "delivered", "seen", "failed"):
        meta = dict(account.meta_data or {})
        subs = meta.get("subscribers_count")
        if event_name == "subscribed" and isinstance(subs, int):
            meta["subscribers_count"] = subs + 1
        elif event_name == "unsubscribed" and isinstance(subs, int):
            meta["subscribers_count"] = max(0, subs - 1)
        account.meta_data = meta
        flag_modified(account, "meta_data")
        await db.commit()
        return response

    if event_name == "conversation_started":
        meta = account.meta_data or {}
        welcome = (meta.get("viber_auto_reply") or {}).get("welcome_message") or ""
        if welcome and event.get("user_id") and not event.get("subscribed"):
            try:
                client = _client_for(account)
                sender = meta.get("bot_name") or account.display_name or "Cloudless"
                await client.send_text(str(event["user_id"]), welcome, sender_name=sender)
                response["welcome_sent"] = True
            except Exception as exc:
                logger.warning(
                    "Viber welcome message failed account=%s: %s",
                    _sanitize(str(account_id)),
                    _sanitize(str(exc)),
                )
                response["welcome_error"] = True
        return response

    if event_name != "message":
        return {**response, "ignored": True}

    text = (event.get("text") or "").strip()
    user_id = event.get("user_id")
    if not text or not user_id:
        return {**response, "ignored": True}

    meta = account.meta_data or {}
    auto_reply = meta.get("viber_auto_reply", {})
    if not auto_reply.get("enabled", False):
        response["auto_reply"] = False
        return response

    try:
        from app.services.viber_chatbot import process_inbound_message

        bot_result = await process_inbound_message(
            account_id=str(account.id),
            team_id=str(account.team_id) if account.team_id else "",
            user_id=str(user_id),
            sender_name=event.get("user_name") or "",
            message_text=text,
            message_token=event.get("message_token"),
            config=auto_reply,
            account_name=account.display_name or account.username or "Cloudless",
        )
        if bot_result.get("skipped") or not bot_result.get("reply"):
            response.update(
                {
                    "auto_reply": True,
                    "skipped": bot_result.get("skipped", False),
                    "reason": bot_result.get("reason", ""),
                }
            )
            return response

        client = _client_for(account)
        sender = meta.get("bot_name") or account.display_name or "Cloudless"
        await client.send_text(str(user_id), bot_result["reply"], sender_name=sender)
        response.update({"auto_reply": True, "replied": True})
        return response
    except Exception as exc:
        logger.error(
            "Viber webhook auto-reply failed account=%s: %s",
            _sanitize(str(account_id)),
            _sanitize(str(exc)),
        )
        response.update({"auto_reply": True, "error": True})
        return response
