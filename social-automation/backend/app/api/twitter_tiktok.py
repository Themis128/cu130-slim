"""Twitter/X and TikTok DM auto-reply API endpoints.

Twitter/X: Uses the official DM API v2 (requires dm.read + dm.write scopes,
OAuth 2.0 user-context). Free tier is read-only; Basic ($200/mo) allows sends.

TikTok: Uses the Business Messaging API (Open Beta in select regions, not EU).
Only for inbound messages (users must message first).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.core.security import decrypt_token
from app.db.session import get_db
from app.models.social_account import SocialAccount
from app.models.user import Team, TeamMember

router = APIRouter()


async def _get_account(
    account_id: uuid.UUID,
    current_user,
    db: AsyncSession,
    platform: str,
) -> SocialAccount:
    """Fetch a social account owned by the current user's team."""
    result = await db.execute(
        select(SocialAccount).join(Team).join(TeamMember)
        .where(
            SocialAccount.id == account_id,
            SocialAccount.platform == platform,
            TeamMember.user_id == current_user.id,
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail=f"{platform.title()} account not found")
    return account


class DMAutoReplyConfig(BaseModel):
    enabled: bool = False
    system_prompt: str = "You are a helpful assistant for {account_name}. Reply concisely and professionally."
    model: str = "@cf/meta/llama-3.1-8b-instruct"
    fallback_text: str = "Thanks for your message! I'll get back to you soon."
    max_tokens: int = 250
    cooldown_seconds: int = 300
    temperature: float = 0.7


# ── Twitter/X DM endpoints ──────────────────────────────────────────────────


@router.get("/twitter/{account_id}/dm/auto-reply")
async def get_twitter_dm_auto_reply(
    account_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the Twitter DM auto-reply configuration for an account."""
    account = await _get_account(account_id, current_user, db, "twitter")
    meta = account.meta_data or {}
    config = meta.get("twitter_auto_reply", {})
    return DMAutoReplyConfig(
        enabled=config.get("enabled", False),
        system_prompt=config.get("system_prompt", DMAutoReplyConfig().system_prompt),
        model=config.get("model", "@cf/meta/llama-3.1-8b-instruct"),
        fallback_text=config.get("fallback_text", DMAutoReplyConfig().fallback_text),
        max_tokens=config.get("max_tokens", 250),
        cooldown_seconds=config.get("cooldown_seconds", 300),
        temperature=config.get("temperature", 0.7),
    )


@router.put("/twitter/{account_id}/dm/auto-reply")
async def update_twitter_dm_auto_reply(
    account_id: uuid.UUID,
    body: DMAutoReplyConfig,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update the Twitter DM auto-reply configuration for an account."""
    account = await _get_account(account_id, current_user, db, "twitter")
    meta = account.meta_data or {}
    meta["twitter_auto_reply"] = body.model_dump()
    account.meta_data = meta
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(account, "meta_data")
    await db.commit()
    return body


@router.get("/twitter/{account_id}/dm/events")
async def list_twitter_dm_events(
    account_id: uuid.UUID,
    max_results: int = 50,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List recent Twitter DM events (requires dm.read scope + paid tier)."""
    account = await _get_account(account_id, current_user, db, "twitter")
    if not account.access_token_enc:
        raise HTTPException(status_code=400, detail="Twitter account has no access token — connect via OAuth first")
    from app.services.twitter_api import TwitterAPIClient, TwitterAPIError

    token = decrypt_token(account.access_token_enc)
    client = TwitterAPIClient(access_token=token)
    try:
        result = await client.list_dm_events(max_results=max_results)
        return result
    except TwitterAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/twitter/{account_id}/dm/send")
async def send_twitter_dm(
    account_id: uuid.UUID,
    body: dict,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Send a Twitter DM to a user (requires dm.write scope + paid tier)."""
    account = await _get_account(account_id, current_user, db, "twitter")
    if not account.access_token_enc:
        raise HTTPException(status_code=400, detail="Twitter account has no access token — connect via OAuth first")
    participant_id = body.get("participant_id")
    text = body.get("text")
    conversation_id = body.get("conversation_id")
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    from app.services.twitter_api import TwitterAPIClient, TwitterAPIError

    token = decrypt_token(account.access_token_enc)
    client = TwitterAPIClient(access_token=token)
    try:
        if conversation_id:
            result = await client.send_dm_to_conversation(conversation_id, text)
        elif participant_id:
            result = await client.send_dm(participant_id, text)
        else:
            raise HTTPException(status_code=400, detail="participant_id or conversation_id is required")
        return result
    except TwitterAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


# ── TikTok DM endpoints ─────────────────────────────────────────────────────


@router.get("/tiktok/{account_id}/dm/auto-reply")
async def get_tiktok_dm_auto_reply(
    account_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the TikTok DM auto-reply configuration for an account."""
    account = await _get_account(account_id, current_user, db, "tiktok")
    meta = account.meta_data or {}
    config = meta.get("tiktok_auto_reply", {})
    return DMAutoReplyConfig(
        enabled=config.get("enabled", False),
        system_prompt=config.get("system_prompt", DMAutoReplyConfig().system_prompt),
        model=config.get("model", "@cf/meta/llama-3.1-8b-instruct"),
        fallback_text=config.get("fallback_text", DMAutoReplyConfig().fallback_text),
        max_tokens=config.get("max_tokens", 250),
        cooldown_seconds=config.get("cooldown_seconds", 300),
        temperature=config.get("temperature", 0.7),
    )


@router.put("/tiktok/{account_id}/dm/auto-reply")
async def update_tiktok_dm_auto_reply(
    account_id: uuid.UUID,
    body: DMAutoReplyConfig,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update the TikTok DM auto-reply configuration for an account."""
    account = await _get_account(account_id, current_user, db, "tiktok")
    meta = account.meta_data or {}
    meta["tiktok_auto_reply"] = body.model_dump()
    account.meta_data = meta
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(account, "meta_data")
    await db.commit()
    return body


@router.get("/tiktok/{account_id}/dm/conversations")
async def list_tiktok_dm_conversations(
    account_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List TikTok DM conversations (requires Business Messaging API access)."""
    account = await _get_account(account_id, current_user, db, "tiktok")
    if not account.access_token_enc:
        raise HTTPException(
            status_code=400,
            detail="TikTok account has no access token. Business Messaging API is in Open Beta (APAC, LATAM, METAP, North America) — not yet available in EU.",
        )
    from app.services.tiktok_api import TikTokAPIClient, TikTokAPIError

    token = decrypt_token(account.access_token_enc)
    open_id = (account.meta_data or {}).get("open_id", account.account_id or "")
    client = TikTokAPIClient(access_token=token, open_id=open_id)
    try:
        result = await client.list_dm_conversations()
        return result
    except TikTokAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/tiktok/{account_id}/dm/send")
async def send_tiktok_dm(
    account_id: uuid.UUID,
    body: dict,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Send a TikTok DM (requires Business Messaging API access)."""
    account = await _get_account(account_id, current_user, db, "tiktok")
    if not account.access_token_enc:
        raise HTTPException(
            status_code=400,
            detail="TikTok account has no access token. Business Messaging API is in Open Beta (APAC, LATAM, METAP, North America) — not yet available in EU.",
        )
    conversation_id = body.get("conversation_id")
    text = body.get("text")
    if not conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id is required")
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    from app.services.tiktok_api import TikTokAPIClient, TikTokAPIError

    token = decrypt_token(account.access_token_enc)
    open_id = (account.meta_data or {}).get("open_id", account.account_id or "")
    client = TikTokAPIClient(access_token=token, open_id=open_id)
    try:
        result = await client.send_dm(conversation_id, {"text": text})
        return result
    except TikTokAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
