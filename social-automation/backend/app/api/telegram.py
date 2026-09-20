"""Telegram Bot API router — credentials, webhook, send, auto-reply, bot.

Official docs: https://core.telegram.org/bots/api

Accounts use ``platform="telegram"`` with bot token stored encrypted.
Inbound updates arrive via ``POST /telegram/webhook/{account_id}`` with
optional ``X-Telegram-Bot-Api-Secret-Token`` verification.
"""

from __future__ import annotations

import logging
import secrets
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
from app.services.telegram_api import (
    TELEGRAM_ALLOWED_UPDATES,
    TelegramAPIClient,
    TelegramAPIError,
    extract_inbound_text_update,
    extract_my_chat_member_update,
)
from app.services.telegram_group_watch import META_KEY as GROUP_WATCH_META_KEY
from app.services.telegram_group_watch import (
    default_group_watch_config,
    get_group_watch_from_meta,
    handle_bot_membership_change,
    handle_mistaken_botfather_command,
    handle_owner_link_command,
    list_buffered_messages,
    normalize_group_watch_config,
    process_group_message_watch,
    send_digest_for_account,
    should_auto_reply_in_group,
    upsert_watched_chat,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _sanitize(text: str, max_len: int = 400) -> str:
    return (text or "").replace("\n", "\\n").replace("\r", "\\r")[:max_len]


async def _get_telegram_account(
    db: AsyncSession,
    account_id: uuid.UUID,
    user: User,
) -> SocialAccount:
    result = await db.execute(select(SocialAccount).where(SocialAccount.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if account.platform != "telegram":
        raise HTTPException(status_code=400, detail="This endpoint requires a Telegram account")
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


def _decrypt_bot_token(account: SocialAccount) -> str:
    meta = account.meta_data or {}
    raw = meta.get("bot_token_enc") or ""
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
        detail="No Telegram bot token stored. Add credentials first.",
    )


def _client_for(account: SocialAccount) -> TelegramAPIClient:
    return TelegramAPIClient(_decrypt_bot_token(account))


def _webhook_base() -> str:
    base = (getattr(settings, "TELEGRAM_WEBHOOK_BASE", None) or "").rstrip("/")
    if base:
        return base
    # Prefer public tunnel / production API host when configured via media public URL
    media = (getattr(settings, "MEDIA_PUBLIC_BASE_URL", None) or "").rstrip("/")
    if media.startswith("https://"):
        # MEDIA_PUBLIC_BASE_URL is often https://social.cloudless.gr/media — strip path
        from urllib.parse import urlparse

        parsed = urlparse(media)
        return f"{parsed.scheme}://{parsed.netloc}/api/v1"
    return "https://social.cloudless.gr/api/v1"


def _webhook_url_for(account_id: uuid.UUID) -> str:
    return f"{_webhook_base()}/telegram/webhook/{account_id}"


def _new_webhook_secret() -> str:
    # Telegram allows A-Z a-z 0-9 _ -
    return secrets.token_urlsafe(32).replace("=", "").replace("/", "_").replace("+", "-")[:64]


# ── Schemas ──────────────────────────────────────────────────────────


class TelegramConnectRequest(BaseModel):
    bot_token: str = Field(..., description="BotFather authentication token")
    set_webhook: bool = Field(True, description="Register HTTPS webhook after connect")


class TelegramCredentialsUpdate(BaseModel):
    bot_token: str = Field(..., description="BotFather authentication token")
    set_webhook: bool = Field(False, description="Also register webhook after update")


class SendMessageRequest(BaseModel):
    chat_id: int | str = Field(..., description="Telegram chat id (user must message the bot first)")
    text: str = Field(..., min_length=1, max_length=4096)
    parse_mode: str | None = None
    disable_notification: bool = False


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


class BotConfig(BaseModel):
    name: str = "Cloudless Assistant"
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


class BotCreateRequest(BaseModel):
    name: str = "Cloudless Assistant"
    personality: str = "professional_friendly"
    language: str = "auto"
    custom_prompt: str | None = None


class WatchedChat(BaseModel):
    chat_id: str
    title: str = ""
    type: str = "supergroup"


class GroupWatchConfig(BaseModel):
    """Keep the owner updated from Telegram groups via Bot API webhooks."""

    enabled: bool = False
    owner_chat_id: str | None = None
    owner_username: str | None = None
    watched_chats: list[WatchedChat] = Field(default_factory=list)
    watch_all_groups: bool = True
    keywords: list[str] = Field(
        default_factory=lambda: list(default_group_watch_config()["keywords"])
    )
    alert_on_bot_mention: bool = True
    alert_on_keywords: bool = True
    forward_alert_messages: bool = False
    digest_enabled: bool = True
    digest_hour: int = Field(9, ge=0, le=23)
    digest_max_messages: int = Field(40, ge=5, le=100)
    auto_reply_groups_only_when_mentioned: bool = True


PERSONALITY_PRESETS = {
    "professional_friendly": (
        "You are {bot_name} for {business_name}. You are professional yet friendly. "
        "Reply in the same language as the incoming message (Greek or English). "
        "Be concise (2-3 sentences). Never make up facts or prices — if unsure, say you will follow up."
    ),
    "casual": (
        "You are {bot_name} for {business_name}. You are casual and approachable. "
        "Reply in the same language as the incoming message. Keep it short."
    ),
    "formal": (
        "You are {bot_name} for {business_name}. You are formal and precise. "
        "Reply in the same language as the incoming message."
    ),
    "support": (
        "You are {bot_name}, a customer support assistant for {business_name}. "
        "Be helpful and patient. Never invent information."
    ),
    "sales": (
        "You are {bot_name}, a sales assistant for {business_name}. "
        "Be enthusiastic but not pushy. Never make up prices."
    ),
}


# ── Connect / credentials ────────────────────────────────────────────


@router.post("/connect", response_model=dict)
async def connect_telegram_bot(
    body: TelegramConnectRequest,
    team_id: TeamId,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Create a Telegram bot account from a BotFather token."""
    await check_quota("social_accounts", team_id, db)
    try:
        client = TelegramAPIClient(body.bot_token)
        me = await client.get_me()
    except (TelegramAPIError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid bot token: {exc}") from exc

    bot_id = str(me.get("id") or "")
    username = me.get("username") or ""
    display = me.get("first_name") or username or "Telegram Bot"
    if not bot_id:
        raise HTTPException(status_code=400, detail="getMe did not return a bot id")

    existing = await db.execute(
        select(SocialAccount).where(
            SocialAccount.team_id == team_id,
            SocialAccount.platform == "telegram",
            SocialAccount.account_id == bot_id,
        )
    )
    account = existing.scalar_one_or_none()
    enc = encrypt_token(body.bot_token)
    secret = _new_webhook_secret()
    meta: dict[str, Any] = {
        "bot_token_enc": enc.decode() if isinstance(enc, bytes) else enc,
        "bot_id": bot_id,
        "bot_username": username,
        "credentials_configured": True,
        "webhook_secret": secret,
        "can_join_groups": me.get("can_join_groups"),
        "can_read_all_group_messages": me.get("can_read_all_group_messages"),
        "supports_inline_queries": me.get("supports_inline_queries"),
    }

    if account:
        account.access_token_enc = enc if isinstance(enc, bytes) else enc.encode()
        account.username = username or account.username
        account.display_name = display
        account.status = "active"
        account.meta_data = {**(account.meta_data or {}), **meta}
        flag_modified(account, "meta_data")
    else:
        account = SocialAccount(
            team_id=team_id,
            platform="telegram",
            account_id=bot_id,
            username=username or None,
            display_name=display,
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
        "bot_username": username,
        "display_name": display,
        "webhook": webhook_result,
    }


@router.put("/{account_id}/credentials", response_model=dict)
async def update_telegram_credentials(
    account_id: uuid.UUID,
    body: TelegramCredentialsUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    account = await _get_telegram_account(db, account_id, user)
    try:
        client = TelegramAPIClient(body.bot_token)
        me = await client.get_me()
    except (TelegramAPIError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid bot token: {exc}") from exc

    bot_id = str(me.get("id") or "")
    username = me.get("username") or ""
    enc = encrypt_token(body.bot_token)
    meta = dict(account.meta_data or {})
    meta.update(
        {
            "bot_token_enc": enc.decode() if isinstance(enc, bytes) else enc,
            "bot_id": bot_id,
            "bot_username": username,
            "credentials_configured": True,
        }
    )
    if not meta.get("webhook_secret"):
        meta["webhook_secret"] = _new_webhook_secret()

    account.access_token_enc = enc if isinstance(enc, bytes) else enc.encode()
    account.account_id = bot_id or account.account_id
    account.username = username or account.username
    account.display_name = me.get("first_name") or username or account.display_name
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
        "bot_username": username,
        "webhook": webhook_result,
    }


async def _register_webhook(account: SocialAccount, client: TelegramAPIClient) -> dict[str, Any]:
    meta = dict(account.meta_data or {})
    secret = meta.get("webhook_secret") or _new_webhook_secret()
    meta["webhook_secret"] = secret
    url = _webhook_url_for(account.id)
    try:
        ok = await client.set_webhook(
            url,
            secret_token=secret,
            allowed_updates=list(TELEGRAM_ALLOWED_UPDATES),
            drop_pending_updates=False,
        )
        meta["webhook_url"] = url
        meta["webhook_set"] = bool(ok)
        account.meta_data = meta
        flag_modified(account, "meta_data")
        info = await client.get_webhook_info()
        return {"ok": bool(ok), "url": url, "info": info}
    except (TelegramAPIError, ValueError) as exc:
        meta["webhook_set"] = False
        meta["webhook_error"] = str(exc)
        account.meta_data = meta
        flag_modified(account, "meta_data")
        return {"ok": False, "url": url, "error": str(exc)}


@router.post("/{account_id}/setup-webhook", response_model=dict)
async def setup_telegram_webhook(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Call official setWebhook for this bot account."""
    account = await _get_telegram_account(db, account_id, user)
    client = _client_for(account)
    result = await _register_webhook(account, client)
    await db.commit()
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error") or "setWebhook failed")
    return {"status": "ok", **result}


@router.post("/{account_id}/delete-webhook", response_model=dict)
async def delete_telegram_webhook(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    account = await _get_telegram_account(db, account_id, user)
    client = _client_for(account)
    try:
        ok = await client.delete_webhook()
    except TelegramAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    meta = dict(account.meta_data or {})
    meta["webhook_set"] = False
    meta.pop("webhook_url", None)
    account.meta_data = meta
    flag_modified(account, "meta_data")
    await db.commit()
    return {"status": "ok", "deleted": bool(ok)}


@router.get("/{account_id}/setup-status", response_model=dict)
async def get_setup_status(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    account = await _get_telegram_account(db, account_id, user)
    meta = account.meta_data or {}
    has_creds = bool(meta.get("credentials_configured") or meta.get("bot_token_enc"))
    webhook_url = meta.get("webhook_url") or _webhook_url_for(account.id)
    me = None
    webhook_info = None
    token_valid = False
    if has_creds:
        try:
            client = _client_for(account)
            me = await client.get_me()
            token_valid = True
            webhook_info = await client.get_webhook_info()
        except Exception as exc:
            logger.debug("Telegram setup-status live check failed: %s", _sanitize(str(exc)))

    live_url = (webhook_info or {}).get("url") or ""
    webhook_ok = bool(live_url) and live_url.startswith("https://")

    next_step = "setup_complete"
    if not has_creds:
        next_step = "Create a bot with @BotFather, then paste the token via Connect / credentials"
    elif not token_valid:
        next_step = "Bot token is invalid — update credentials"
    elif not webhook_ok:
        next_step = (
            "Call Setup Webhook so Telegram can POST updates to SocialAuto "
            "(requires public HTTPS, e.g. social.cloudless.gr)"
        )
    elif not (meta.get("telegram_auto_reply") or {}).get("enabled"):
        next_step = "Enable auto-reply or create a bot persona"

    return {
        "account_exists": True,
        "credentials_configured": has_creds,
        "token_valid": token_valid,
        "webhook_set": webhook_ok or bool(meta.get("webhook_set")),
        "webhook_url": live_url or webhook_url,
        "webhook_info": webhook_info,
        "bot": me,
        "bot_username": meta.get("bot_username") or account.username,
        "bot_id": meta.get("bot_id") or account.account_id,
        "can_send_messages": token_valid,
        "next_step": next_step,
        "note": (
            "Bots cannot start chats — the user must message the bot first "
            "(https://core.telegram.org/bots)."
        ),
    }


# ── Send ─────────────────────────────────────────────────────────────


@router.post("/{account_id}/send", response_model=dict)
async def send_message(
    account_id: uuid.UUID,
    body: SendMessageRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    account = await _get_telegram_account(db, account_id, user)
    client = _client_for(account)
    try:
        result = await client.send_message(
            body.chat_id,
            body.text,
            parse_mode=body.parse_mode,
            disable_notification=body.disable_notification,
        )
    except (TelegramAPIError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"status": "ok", "message": result}


# ── Auto-reply ───────────────────────────────────────────────────────


@router.get("/{account_id}/auto-reply", response_model=AutoReplyConfig)
async def get_auto_reply_config(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    account = await _get_telegram_account(db, account_id, user)
    config = (account.meta_data or {}).get("telegram_auto_reply", {})
    return AutoReplyConfig(**config)


@router.put("/{account_id}/auto-reply", response_model=AutoReplyConfig)
async def update_auto_reply_config(
    account_id: uuid.UUID,
    body: AutoReplyConfig,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    account = await _get_telegram_account(db, account_id, user)
    if body.enabled:
        from app.api.deps import check_plan_feature
        await check_plan_feature("dm_auto_reply", account.team_id, db)
    meta = dict(account.meta_data or {})
    meta["telegram_auto_reply"] = body.model_dump()
    account.meta_data = meta
    flag_modified(account, "meta_data")
    await db.commit()
    return body


# ── Bot builder ──────────────────────────────────────────────────────


@router.post("/{account_id}/bot/create")
async def create_bot(
    account_id: uuid.UUID,
    req: BotCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    account = await _get_telegram_account(db, account_id, user)
    business_name = account.display_name or account.username or "Cloudless"
    if req.custom_prompt:
        system_prompt = req.custom_prompt.replace("{business_name}", business_name)
    else:
        preset = PERSONALITY_PRESETS.get(req.personality, PERSONALITY_PRESETS["professional_friendly"])
        system_prompt = preset.replace("{bot_name}", req.name).replace("{business_name}", business_name)
    if req.language and req.language != "auto":
        lang_names = {"en": "English", "el": "Greek (Ελληνικά)"}
        system_prompt += f" Always reply in {lang_names.get(req.language, req.language)}."

    from app.api.deps import check_plan_feature
    await check_plan_feature("dm_auto_reply", account.team_id, db)
    bot_config = BotConfig(name=req.name, enabled=True, system_prompt=system_prompt)
    meta = dict(account.meta_data or {})
    meta["telegram_bot"] = bot_config.model_dump()
    meta["telegram_auto_reply"] = {
        "enabled": True,
        "system_prompt": system_prompt,
        "model": bot_config.model,
        "fallback_text": bot_config.fallback_text,
        "max_tokens": bot_config.max_tokens,
        "cooldown_seconds": bot_config.cooldown_seconds,
        "temperature": bot_config.temperature,
        "reply_to_spam": bot_config.reply_to_spam,
        "reply_to_greetings": bot_config.reply_to_greetings,
    }
    account.meta_data = meta
    flag_modified(account, "meta_data")
    await db.commit()

    brand_indexed = 0
    try:
        from app.models.brand import Brand
        from app.services.telegram_chatbot import index_brand_knowledge

        brand = (
            await db.execute(select(Brand).where(Brand.team_id == account.team_id))
        ).scalars().first()
        if brand:
            brand_indexed = await index_brand_knowledge(
                str(account.team_id),
                {
                    "name": brand.name,
                    "tagline": getattr(brand, "tagline", None),
                    "positioning_statement": getattr(brand, "positioning_statement", None),
                    "mission": getattr(brand, "mission", None),
                    "industry": getattr(brand, "industry", None),
                    "values": getattr(brand, "values", []),
                    "target_audience": getattr(brand, "target_audience", {}),
                    "competitor_names": getattr(brand, "competitor_names", []),
                },
            )
    except Exception as exc:
        logger.warning("Telegram brand indexing failed: %s", _sanitize(str(exc)))

    return {"status": "ok", "bot": bot_config.model_dump(), "brand_indexed": brand_indexed}


@router.get("/{account_id}/bot")
async def get_bot(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    account = await _get_telegram_account(db, account_id, user)
    bot = (account.meta_data or {}).get("telegram_bot")
    if not bot:
        return {"exists": False, "bot": None}
    return {"exists": True, "bot": bot}


@router.put("/{account_id}/bot")
async def update_bot(
    account_id: uuid.UUID,
    config: BotConfig,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    account = await _get_telegram_account(db, account_id, user)
    meta = dict(account.meta_data or {})
    meta["telegram_bot"] = config.model_dump()
    meta["telegram_auto_reply"] = {
        "enabled": config.enabled,
        "system_prompt": config.system_prompt,
        "model": config.model,
        "fallback_text": config.fallback_text,
        "max_tokens": config.max_tokens,
        "cooldown_seconds": config.cooldown_seconds,
        "temperature": config.temperature,
        "reply_to_spam": config.reply_to_spam,
        "reply_to_greetings": config.reply_to_greetings,
    }
    account.meta_data = meta
    flag_modified(account, "meta_data")
    await db.commit()
    return {"status": "ok", "bot": config.model_dump()}


@router.post("/{account_id}/bot/activate")
async def activate_bot(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    account = await _get_telegram_account(db, account_id, user)
    from app.api.deps import check_plan_feature
    await check_plan_feature("dm_auto_reply", account.team_id, db)
    meta = dict(account.meta_data or {})
    bot = meta.get("telegram_bot")
    if not bot:
        raise HTTPException(status_code=400, detail="No bot found — create one first")
    bot["enabled"] = True
    meta["telegram_bot"] = bot
    auto = meta.get("telegram_auto_reply", {})
    auto["enabled"] = True
    meta["telegram_auto_reply"] = auto
    account.meta_data = meta
    flag_modified(account, "meta_data")
    await db.commit()
    return {"status": "ok", "enabled": True}


@router.post("/{account_id}/bot/deactivate")
async def deactivate_bot(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    account = await _get_telegram_account(db, account_id, user)
    meta = dict(account.meta_data or {})
    bot = meta.get("telegram_bot")
    if not bot:
        raise HTTPException(status_code=400, detail="No bot found")
    bot["enabled"] = False
    meta["telegram_bot"] = bot
    auto = meta.get("telegram_auto_reply", {})
    auto["enabled"] = False
    meta["telegram_auto_reply"] = auto
    account.meta_data = meta
    flag_modified(account, "meta_data")
    await db.commit()
    return {"status": "ok", "enabled": False}


@router.get("/{account_id}/bot/personalities")
async def list_personalities(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _get_telegram_account(db, account_id, user)
    return {"personalities": list(PERSONALITY_PRESETS.keys())}


# ── Group watch (digests + keyword alerts) ───────────────────────────


@router.get("/{account_id}/group-watch", response_model=GroupWatchConfig)
async def get_group_watch(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    account = await _get_telegram_account(db, account_id, user)
    cfg = get_group_watch_from_meta(account.meta_data)
    return GroupWatchConfig(**{
        **cfg,
        "watched_chats": [
            WatchedChat(**c) if isinstance(c, dict) else WatchedChat(chat_id=str(c))
            for c in (cfg.get("watched_chats") or [])
        ],
    })


@router.put("/{account_id}/group-watch", response_model=GroupWatchConfig)
async def update_group_watch(
    account_id: uuid.UUID,
    body: GroupWatchConfig,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    account = await _get_telegram_account(db, account_id, user)
    meta = dict(account.meta_data or {})
    normalized = normalize_group_watch_config(body.model_dump())
    meta[GROUP_WATCH_META_KEY] = normalized
    account.meta_data = meta
    flag_modified(account, "meta_data")
    # Refresh webhook so my_chat_member is subscribed
    try:
        client = _client_for(account)
        await _register_webhook(account, client)
    except Exception as exc:
        logger.warning("group-watch webhook refresh failed: %s", _sanitize(str(exc)))
    await db.commit()
    return GroupWatchConfig(**{
        **normalized,
        "watched_chats": [
            WatchedChat(**c) if isinstance(c, dict) else WatchedChat(chat_id=str(c))
            for c in (normalized.get("watched_chats") or [])
        ],
    })


@router.post("/{account_id}/group-watch/add-chat")
async def add_watched_chat(
    account_id: uuid.UUID,
    body: WatchedChat,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    account = await _get_telegram_account(db, account_id, user)
    cfg = get_group_watch_from_meta(account.meta_data)
    title = body.title
    chat_type = body.type
    try:
        client = _client_for(account)
        info = await client.get_chat(body.chat_id)
        title = title or info.get("title") or info.get("username") or title
        chat_type = info.get("type") or chat_type
    except (TelegramAPIError, ValueError) as exc:
        logger.info("getChat optional failed for watch add: %s", _sanitize(str(exc)))
    cfg = upsert_watched_chat(
        cfg, chat_id=body.chat_id, title=title or "", chat_type=chat_type or "supergroup"
    )
    cfg["enabled"] = True
    meta = dict(account.meta_data or {})
    meta[GROUP_WATCH_META_KEY] = cfg
    account.meta_data = meta
    flag_modified(account, "meta_data")
    await db.commit()
    return {"status": "ok", "config": cfg}


@router.post("/{account_id}/group-watch/digest-now")
async def trigger_group_digest_now(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    account = await _get_telegram_account(db, account_id, user)
    cfg = get_group_watch_from_meta(account.meta_data)
    client = _client_for(account)
    result = await send_digest_for_account(
        client=client,
        account_id=str(account.id),
        cfg=cfg,
        force=True,
    )
    return {"status": "ok", **result}


@router.get("/{account_id}/group-watch/activity")
async def get_group_watch_activity(
    account_id: uuid.UUID,
    chat_id: str | None = None,
    limit: int = 40,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    account = await _get_telegram_account(db, account_id, user)
    cfg = get_group_watch_from_meta(account.meta_data)
    chats = cfg.get("watched_chats") or []
    if chat_id:
        messages = await list_buffered_messages(str(account.id), chat_id, limit=limit)
        return {"chat_id": chat_id, "messages": messages}
    out = []
    for c in chats:
        cid = str(c.get("chat_id") if isinstance(c, dict) else c)
        msgs = await list_buffered_messages(str(account.id), cid, limit=min(limit, 20))
        out.append(
            {
                "chat_id": cid,
                "title": c.get("title") if isinstance(c, dict) else "",
                "buffered_count": len(msgs),
                "recent": msgs[:5],
            }
        )
    return {
        "owner_chat_id": cfg.get("owner_chat_id"),
        "enabled": cfg.get("enabled"),
        "chats": out,
        "link_hint": "Open a private chat with the bot and send /linkowner",
    }


@router.post("/{account_id}/group-watch/setup-links")
async def setup_group_watch_links(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Register bot commands and return official deep links for one-tap setup.

    Telegram does not allow bots to disable Group Privacy or join groups by
    themselves — those require the account owner. Deep links minimize clicks:
    - ``?start=linkowner`` opens a private chat and links the owner
    - ``?startgroup=watch`` opens the “add bot to group” picker
    Docs: https://core.telegram.org/bots/features#deep-linking
    """
    from app.services.telegram_group_watch import bot_deep_links, ensure_bot_commands

    account = await _get_telegram_account(db, account_id, user)
    meta = dict(account.meta_data or {})
    username = meta.get("bot_username") or account.username or ""
    client = _client_for(account)
    if not username:
        try:
            me = await client.get_me()
            username = me.get("username") or ""
            if username:
                meta["bot_username"] = username
                account.meta_data = meta
                flag_modified(account, "meta_data")
        except TelegramAPIError:
            pass
    commands_ok = await ensure_bot_commands(client)
    # Keep webhook subscribed to my_chat_member
    webhook = await _register_webhook(account, client)
    await db.commit()
    links = bot_deep_links(username)
    cfg = get_group_watch_from_meta(account.meta_data)
    return {
        "status": "ok",
        "bot_username": username,
        "commands_registered": commands_ok,
        "webhook": {"ok": webhook.get("ok"), "allowed_updates": (webhook.get("info") or {}).get("allowed_updates")},
        "links": links,
        "owner_linked": bool(cfg.get("owner_chat_id")),
        "owner_chat_id": cfg.get("owner_chat_id"),
        "checklist": [
            {
                "id": "link_owner",
                "done": bool(cfg.get("owner_chat_id")),
                "action": "Open link — taps Start in Telegram",
                "url": links.get("link_owner"),
            },
            {
                "id": "add_to_group",
                "done": bool(cfg.get("watched_chats")),
                "action": "Open link — pick Visibility Era 2.0",
                "url": links.get("add_to_group"),
            },
            {
                "id": "group_privacy",
                "done": False,
                "action": "BotFather → Bot Settings → Group Privacy → Turn off (API cannot do this)",
                "url": links.get("botfather_privacy"),
            },
        ],
    }


# ── Thread pause / resume ────────────────────────────────────────────


@router.post("/{account_id}/threads/pause")
async def pause_thread(
    account_id: uuid.UUID,
    body: dict,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _get_telegram_account(db, account_id, user)
    chat_id = str(body.get("chat_id") or "")
    if not chat_id:
        raise HTTPException(status_code=400, detail="chat_id is required")
    from app.services.telegram_chatbot import pause_thread as _pause

    await _pause(str(account_id), chat_id)
    return {"status": "ok", "paused": True}


@router.post("/{account_id}/threads/resume")
async def resume_thread(
    account_id: uuid.UUID,
    body: dict,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _get_telegram_account(db, account_id, user)
    chat_id = str(body.get("chat_id") or "")
    if not chat_id:
        raise HTTPException(status_code=400, detail="chat_id is required")
    from app.services.telegram_chatbot import resume_thread as _resume

    await _resume(str(account_id), chat_id)
    return {"status": "ok", "paused": False}


# ── Webhook ──────────────────────────────────────────────────────────


@router.post("/webhook/{account_id}", response_model=dict)
async def receive_webhook(
    account_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_telegram_bot_api_secret_token: str | None = Header(
        None, alias="X-Telegram-Bot-Api-Secret-Token"
    ),
):
    """Receive Telegram Update JSON (official setWebhook push)."""
    result = await db.execute(
        select(SocialAccount).where(
            SocialAccount.id == account_id,
            SocialAccount.platform == "telegram",
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Unknown Telegram account")

    expected = (account.meta_data or {}).get("webhook_secret") or ""
    if expected:
        if not x_telegram_bot_api_secret_token or not secrets.compare_digest(
            expected, x_telegram_bot_api_secret_token
        ):
            raise HTTPException(status_code=403, detail="Invalid webhook secret token")

    try:
        update = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    if not isinstance(update, dict):
        return {"status": "ok", "ignored": True}

    meta = dict(account.meta_data or {})
    watch_cfg = get_group_watch_from_meta(meta)
    bot_username = meta.get("bot_username") or account.username
    response: dict[str, Any] = {"status": "ok"}

    # ── my_chat_member: bot added/removed/promoted in a group ──
    membership = extract_my_chat_member_update(update)
    if membership:
        try:
            client = _client_for(account)
            watch_cfg, info = await handle_bot_membership_change(
                client=client, cfg=watch_cfg, event=membership
            )
            meta[GROUP_WATCH_META_KEY] = watch_cfg
            account.meta_data = meta
            flag_modified(account, "meta_data")
            await db.commit()
            response["membership"] = info
        except Exception as exc:
            logger.warning(
                "Telegram membership handler failed account=%s: %s",
                _sanitize(str(account_id)),
                _sanitize(str(exc)),
            )
            response["membership_error"] = True
        return response

    inbound = extract_inbound_text_update(update)
    if not inbound:
        return {"status": "ok", "ignored": True}

    if inbound.get("is_bot"):
        return {"status": "ok", "ignored": True, "reason": "bot_sender"}

    # ── BotFather cmds typed here by mistake (/mybots etc.) ──
    try:
        client = _client_for(account)
        if await handle_mistaken_botfather_command(client=client, inbound=inbound):
            return {"status": "ok", "redirected_botfather": True}
    except Exception as exc:
        logger.warning(
            "Telegram botfather redirect failed account=%s: %s",
            _sanitize(str(account_id)),
            _sanitize(str(exc)),
        )

    # ── Owner link (/start /linkowner) in private chat ──
    try:
        client = _client_for(account)
        watch_cfg, linked = await handle_owner_link_command(
            client=client,
            inbound=inbound,
            cfg=watch_cfg,
            bot_username=bot_username,
        )
        if linked:
            meta[GROUP_WATCH_META_KEY] = watch_cfg
            account.meta_data = meta
            flag_modified(account, "meta_data")
            await db.commit()
            return {"status": "ok", "owner_linked": True, "owner_chat_id": watch_cfg.get("owner_chat_id")}
    except Exception as exc:
        logger.warning(
            "Telegram owner link failed account=%s: %s",
            _sanitize(str(account_id)),
            _sanitize(str(exc)),
        )

    # ── Group watch: buffer + keyword/mention alerts ──
    chat_type = inbound.get("chat_type")
    if chat_type in {"group", "supergroup"}:
        try:
            client = _client_for(account)
            # Auto-register chat title when watching all groups
            if watch_cfg.get("enabled") and watch_cfg.get("watch_all_groups", True):
                watch_cfg = upsert_watched_chat(
                    watch_cfg,
                    chat_id=inbound["chat_id"],
                    title=inbound.get("chat_title") or "",
                    chat_type=chat_type,
                )
                meta[GROUP_WATCH_META_KEY] = watch_cfg
                account.meta_data = meta
                flag_modified(account, "meta_data")
                await db.commit()
            watch_result = await process_group_message_watch(
                client=client,
                account_id=str(account.id),
                cfg=watch_cfg,
                inbound=inbound,
                bot_username=bot_username,
            )
            response["group_watch"] = watch_result
        except Exception as exc:
            logger.warning(
                "Telegram group watch failed account=%s: %s",
                _sanitize(str(account_id)),
                _sanitize(str(exc)),
            )
            response["group_watch_error"] = True

        if not should_auto_reply_in_group(watch_cfg, inbound, bot_username):
            response["auto_reply"] = False
            response["reason"] = "group_requires_mention"
            return response

    auto_reply = meta.get("telegram_auto_reply", {})
    if not auto_reply.get("enabled", False):
        response["auto_reply"] = False
        return response

    try:
        from app.services.telegram_chatbot import process_inbound_message

        bot_result = await process_inbound_message(
            account_id=str(account.id),
            team_id=str(account.team_id) if account.team_id else "",
            chat_id=str(inbound["chat_id"]),
            sender_name=inbound.get("sender_name") or "",
            message_text=inbound["text"],
            message_id=inbound.get("message_id"),
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
        await client.send_message(
            inbound["chat_id"],
            bot_result["reply"],
            reply_to_message_id=inbound.get("message_id"),
        )
        response.update({"auto_reply": True, "replied": True})
        return response
    except Exception as exc:
        logger.error(
            "Telegram webhook auto-reply failed account=%s: %s",
            _sanitize(str(account_id)),
            _sanitize(str(exc)),
        )
        response.update({"auto_reply": True, "error": True})
        return response
