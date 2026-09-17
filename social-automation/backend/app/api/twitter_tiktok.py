"""Twitter/X and TikTok DM auto-reply API endpoints.

Twitter/X: Uses the official DM API v2 (requires dm.read + dm.write scopes,
OAuth 2.0 user-context). Free tier is read-only; Basic ($200/mo) allows sends.

TikTok: Uses the Business Messaging API (Open Beta in select regions, not EU).
Only for inbound messages (users must message first).
"""
from __future__ import annotations

import uuid
from typing import Any

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
    if body.enabled:
        from app.api.deps import check_plan_feature
        await check_plan_feature("dm_auto_reply", account.team_id, db)
    meta = account.meta_data or {}
    meta["twitter_auto_reply"] = body.model_dump()
    account.meta_data = meta
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(account, "meta_data")
    await db.commit()
    return body


def _twitter_bridge() -> Any:
    """Return a browser-bridge session for the Twitter DM endpoints.

    The bridge drives x.com/messages through the logged-in web session —
    no X API credits needed. ``browser_session`` takes the exclusive
    browser lock so we don't fight the polling workers.
    """
    from app.core.config import get_settings
    from app.services.browser_bridge import BrowserBridgeClient
    from app.services.browser_orchestrator import browser_session

    return browser_session(
        "twitter", BrowserBridgeClient(get_settings(, platform='twitter').BROWSER_BRIDGE_URL)
    )


@router.get("/twitter/{account_id}/dm/events")
async def list_twitter_dm_events(
    account_id: uuid.UUID,
    max_results: int = 50,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List recent Twitter DM conversations via the browser bridge.

    Uses the logged-in x.com web session (free path) instead of the
    metered X API — the official dm_events endpoint requires paid API
    credits.
    """
    await _get_account(account_id, current_user, db, "twitter")
    try:
        async with _twitter_bridge() as bridge:
            result = await bridge.get_twitter_dm_conversations()
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Browser bridge unavailable or no x.com session: {str(exc)[:300]}",
        ) from exc
    conversations = (result or {}).get("conversations", [])[:max_results]
    return {"data": conversations, "meta": {"result_count": len(conversations), "source": "browser_bridge"}}


def _twitter_thread_id(account: SocialAccount, participant_id: str) -> str:
    """Build an x.com DM thread URL id from a participant user id.

    X web thread ids are ``{lower_user_id}-{higher_user_id}``.
    """
    own = str(account.account_id or "")
    pair = sorted([own, str(participant_id)], key=int)
    return f"{pair[0]}-{pair[1]}"


@router.post("/twitter/{account_id}/dm/send")
async def send_twitter_dm(
    account_id: uuid.UUID,
    body: dict,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Send a Twitter DM via the browser bridge (free path).

    Accepts ``conversation_id``/``thread_id`` (x.com thread id or URL)
    or ``participant_id`` (numeric X user id — converted to a thread id).
    """
    account = await _get_account(account_id, current_user, db, "twitter")
    text = body.get("text")
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    thread_id = body.get("conversation_id") or body.get("thread_id")
    if not thread_id and body.get("participant_id"):
        participant = str(body["participant_id"]).strip()
        if not participant.isdigit():
            raise HTTPException(status_code=400, detail="participant_id must be a numeric X user id")
        if not account.account_id:
            raise HTTPException(status_code=400, detail="account_id missing — cannot derive thread id")
        thread_id = _twitter_thread_id(account, participant)
    if not thread_id:
        raise HTTPException(status_code=400, detail="conversation_id, thread_id or participant_id is required")
    # Accept full x.com URLs too
    if "/messages/" in str(thread_id):
        thread_id = str(thread_id).rsplit("/messages/", 1)[-1].split("?")[0].strip("/")

    async with _twitter_bridge() as bridge:
        result = await bridge.send_twitter_dm_message(str(thread_id), text)
    if isinstance(result, dict) and result.get("error"):
        raise HTTPException(status_code=502, detail=result["error"])
    return {"status": "sent", "thread_id": thread_id, "source": "browser_bridge", "result": result}


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
    if body.enabled:
        from app.api.deps import check_plan_feature
        await check_plan_feature("dm_auto_reply", account.team_id, db)
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
        raise HTTPException(status_code=exc.status_code, detail=str(exc)[:400]) from exc


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
        raise HTTPException(status_code=exc.status_code, detail=str(exc)[:400]) from exc
