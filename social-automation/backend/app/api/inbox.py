"""Unified inbox API — aggregates conversations across all platforms.

Provides a single endpoint to view DMs/conversations from:
- Facebook Page Messenger (official Graph API)
- Facebook Personal Messenger (browser bridge)
- Instagram DMs (Instagram Messaging API)
- WhatsApp (Cloud API)
- Threads (browser bridge, when available)

This is the backend foundation for a unified inbox UI. Each conversation
is normalized to a common schema with platform, sender, preview, and
unread status.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import and_, desc, func, select

from app.api.deps import get_current_team_id, get_current_user
from app.db.session import async_session_maker
from app.models.social_account import SocialAccount
from app.models.user import User
from app.models.whatsapp_message import WhatsAppMessage
from app.services.browser_bridge import BrowserBridgeClient
from app.services.instagram_api import InstagramAPIClient
from app.services.messenger_api import MessengerAPIClient

logger = logging.getLogger(__name__)
router = APIRouter()

# Short server-side cache — the inbox page polls every 30s and each fetch
# fans out to external APIs (Graph + browser bridge), so repeat calls
# within a minute return the previous result.
_INBOX_CACHE_TTL_S = 60.0
_inbox_cache: dict[str, tuple[float, UnifiedInboxResponse]] = {}


class UnifiedConversation(BaseModel):
    """Normalized conversation across all platforms."""
    platform: str  # messenger, instagram, whatsapp, threads, personal_messenger
    account_id: str
    account_name: str
    thread_id: str | None = None
    sender_name: str = ""
    preview: str = ""
    unread: bool = False
    timestamp: str | None = None
    url: str | None = None
    e2ee: bool = False


class UnifiedInboxResponse(BaseModel):
    conversations: list[UnifiedConversation]
    total: int
    by_platform: dict[str, int]


@router.get("/inbox", response_model=UnifiedInboxResponse)
async def get_unified_inbox(
    current_user: User = Depends(get_current_user),
    team_id: uuid.UUID = Depends(get_current_team_id),
) -> UnifiedInboxResponse:
    """Get all conversations across all connected platforms in one view.

    Aggregates conversations from:
    - Facebook Page Messenger (Graph API)
    - Facebook Personal Messenger (browser bridge)
    - Instagram DMs (Instagram Messaging API)
    - WhatsApp (Cloud API, when configured)

    Failed platform fetches are skipped (non-fatal) so the inbox always
    returns available conversations even if one platform is down.
    """
    cache_key = str(team_id)
    cached = _inbox_cache.get(cache_key)
    if cached and (time.monotonic() - cached[0]) < _INBOX_CACHE_TTL_S:
        return cached[1]

    conversations: list[UnifiedConversation] = []

    # Fetch all social accounts for the user's team
    async with async_session_maker() as db:
        result = await db.execute(
            select(SocialAccount).where(SocialAccount.team_id == team_id)
        )
        accounts = result.scalars().all()

    # Per-platform fetch budget — a wedged platform (e.g. busy browser
    # bridge) must not stall the whole inbox.
    FETCH_TIMEOUT_S = 30.0

    # Group accounts by platform
    tasks: list[asyncio.Task] = []
    for account in accounts:
        platform = (account.platform or "").lower()
        account_type = (account.account_type or "").lower()
        if platform == "facebook" and account_type == "page":
            coro = _fetch_page_messenger(account)
        elif platform == "facebook" and account_type == "user":
            coro = _fetch_personal_messenger(account)
        elif platform == "instagram":
            coro = _fetch_instagram_dms(account)
        elif platform == "whatsapp":
            coro = _fetch_whatsapp(account)
        else:
            continue
        tasks.append(asyncio.create_task(asyncio.wait_for(coro, timeout=FETCH_TIMEOUT_S)))

    # Run all fetches in parallel
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, list):
            conversations.extend(result)
        elif isinstance(result, Exception):
            logger.info("Inbox fetch error (non-fatal): %s", result)

    # Sort by unread first, then by name
    conversations.sort(key=lambda c: (not c.unread, c.sender_name.lower()))

    by_platform: dict[str, int] = {}
    for c in conversations:
        by_platform[c.platform] = by_platform.get(c.platform, 0) + 1

    response = UnifiedInboxResponse(
        conversations=conversations,
        total=len(conversations),
        by_platform=by_platform,
    )
    _inbox_cache[cache_key] = (time.monotonic(), response)
    return response


async def _fetch_page_messenger(account: SocialAccount) -> list[UnifiedConversation]:
    """Fetch conversations from a Facebook Page Messenger (Graph API)."""
    try:
        meta = account.meta_data or {}
        page_token = (
            meta.get("page_token")
            or meta.get("page_access_token")
            or meta.get("access_token")
        )
        if not page_token:
            return []
        # page_token is often stored encrypted in meta_data
        if isinstance(page_token, str) and not page_token.startswith("EA"):
            try:
                from app.core.security import decrypt_token
                page_token = decrypt_token(page_token)
            except Exception:
                pass  # might be plaintext
        page_id = account.account_id
        if not page_id:
            return []
        client = MessengerAPIClient(
            access_token=page_token,
            page_id=str(page_id),
        )
        conversations = await client.get_conversations(limit=25)
        convos = []
        for convo in conversations:
            participants = convo.get("participants", {}).get("data", [])
            sender = participants[0] if participants else {}
            unread_count = convo.get("unread_count") or 0
            convos.append(UnifiedConversation(
                platform="messenger",
                account_id=str(account.id),
                account_name=account.display_name or "Facebook Page",
                thread_id=convo.get("id"),
                sender_name=sender.get("name", "Unknown"),
                preview=convo.get("snippet", ""),
                unread=bool(unread_count),
                timestamp=convo.get("updated_time"),
            ))
        return convos
    except Exception as exc:
        logger.debug("Page Messenger fetch failed for %s: %s", account.id, exc)
        return []


async def _fetch_personal_messenger(account: SocialAccount) -> list[UnifiedConversation]:
    """Fetch conversations from personal Facebook Messenger (browser bridge)."""
    try:
        bridge = BrowserBridgeClient("http://browser-novnc:9223", platform="facebook")
        result = await bridge.get_personal_messenger_conversations_fast()
        convos = []
        for convo in result.get("conversations", []):
            convos.append(UnifiedConversation(
                platform="personal_messenger",
                account_id=str(account.id),
                account_name=account.display_name or "Personal Messenger",
                thread_id=convo.get("thread_id"),
                sender_name=convo.get("name", "Unknown"),
                preview=convo.get("preview", ""),
                unread=convo.get("unread", False),
                url=convo.get("url"),
                e2ee=convo.get("e2ee", False),
            ))
        return convos
    except Exception as exc:
        logger.debug("Personal Messenger fetch failed for %s: %s", account.id, exc)
        return []


async def _fetch_instagram_dms(account: SocialAccount) -> list[UnifiedConversation]:
    """Fetch Instagram DM conversations (Instagram Messaging API)."""
    try:
        from app.core.security import decrypt_token

        meta = account.meta_data or {}
        access_token = meta.get("access_token")
        if not access_token and account.access_token_enc:
            try:
                access_token = decrypt_token(account.access_token_enc)
            except Exception:
                access_token = None
        ig_user_id = (
            meta.get("ig_user_id")
            or meta.get("instagram_user_id")
            or meta.get("ig_business_id")
            or account.account_id
        )
        if not access_token or not ig_user_id:
            return []
        client = InstagramAPIClient(
            access_token=access_token,
            ig_user_id=str(ig_user_id),
            use_business_login_api=meta.get("login_type") == "business_login",
        )
        result = await client.get_conversations(limit=25)
        own_username = (account.username or "").lower()
        own_ids = {str(ig_user_id)}
        convos = []
        for convo in result.get("data", []):
            participants = convo.get("participants", {}).get("data", [])
            # participants[0] is our own business account — the customer is
            # the other participant.
            others = [
                p for p in participants
                if str(p.get("id", "")) not in own_ids
                and (p.get("username") or "").lower() != own_username
            ]
            sender = others[0] if others else (participants[0] if participants else {})
            messages = convo.get("messages", {}).get("data", [])
            last_msg = messages[0] if messages else {}
            convos.append(UnifiedConversation(
                platform="instagram",
                account_id=str(account.id),
                account_name=account.display_name or "Instagram",
                thread_id=convo.get("id"),
                sender_name=sender.get("username", "Unknown"),
                preview=last_msg.get("message") or "[media]",
                unread=False,
                timestamp=last_msg.get("created_time"),
            ))
        return convos
    except Exception as exc:
        logger.debug("Instagram DM fetch failed for %s: %s", account.id, exc)
        return []


async def _fetch_whatsapp(account: SocialAccount) -> list[UnifiedConversation]:
    """Fetch WhatsApp conversations from persisted webhook/send records.

    The Cloud API has no "list conversations" endpoint, so threads are
    built from ``whatsapp_messages`` rows written by the webhook and the
    send endpoints — one conversation per remote phone number, ordered by
    the latest message.
    """
    try:
        async with async_session_maker() as db:
            latest = (
                select(
                    WhatsAppMessage.sender_phone.label("peer"),
                    func.max(WhatsAppMessage.created_at).label("mx"),
                )
                .where(WhatsAppMessage.social_account_id == account.id)
                .group_by(WhatsAppMessage.sender_phone)
                .subquery()
            )
            rows = (
                await db.execute(
                    select(WhatsAppMessage)
                    .join(
                        latest,
                        and_(
                            WhatsAppMessage.sender_phone == latest.c.peer,
                            WhatsAppMessage.created_at == latest.c.mx,
                        ),
                    )
                    .order_by(desc(WhatsAppMessage.created_at))
                    .limit(50)
                )
            ).scalars().all()
            if not rows:
                return []

            # Best display name per peer = most recent inbound sender_name.
            names = (
                await db.execute(
                    select(WhatsAppMessage.sender_phone, WhatsAppMessage.sender_name)
                    .where(
                        WhatsAppMessage.social_account_id == account.id,
                        WhatsAppMessage.direction == "inbound",
                        WhatsAppMessage.sender_name != "",
                    )
                    .order_by(desc(WhatsAppMessage.created_at))
                )
            ).all()
        name_by_peer: dict[str, str] = {}
        for phone, name in names:
            name_by_peer.setdefault(phone, name)

        convos = []
        for m in rows:
            peer = m.sender_phone
            body = m.message_text or f"[{m.message_type}]"
            if m.direction == "outbound":
                body = f"You: {body}"
            convos.append(UnifiedConversation(
                platform="whatsapp",
                account_id=str(account.id),
                account_name=account.display_name or "WhatsApp",
                thread_id=peer,
                sender_name=name_by_peer.get(peer) or peer,
                preview=body,
                unread=False,
                timestamp=m.created_at.isoformat() if m.created_at else None,
            ))
        return convos
    except Exception as exc:
        logger.debug("WhatsApp inbox fetch failed for %s: %s", account.id, exc)
        return []
