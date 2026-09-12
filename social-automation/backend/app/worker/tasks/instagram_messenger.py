"""Celery task — Instagram DM chatbot via Instagram Messaging API.

Instagram uses the same Messenger Platform API as Facebook Pages for DMs.
Requires the `instagram_business_manage_messages` permission and a
connected Instagram Business account with an access token.

For personal Instagram accounts without API access, this task gracefully
skips (no token = no API calls).

Flow:
    1. Find Instagram accounts with auto-reply enabled
    2. For each account with an access token:
       a. Fetch recent conversations via Instagram Messaging API
       b. For each conversation with new messages:
          - Read messages
          - Find last inbound message
          - Check if already replied (seen tracking)
          - Check per-conversation cooldown (Redis)
          - Generate AI reply (Cloudflare Workers AI)
          - Send reply via API
          - Mark as seen + set cooldown
    3. For accounts without a token: skip gracefully (log info)

State tracking:
    ``meta_data.instagram_auto_reply`` stores bot config
    ``meta_data.instagram_messenger_seen`` stores {conversation_id: last_text}

The task runs every 3 minutes via Celery beat.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.security import decrypt_token
from app.models.social_account import SocialAccount
from app.services.messenger_chatbot import (
    check_cooldown,
    generate_contextual_reply,
    is_thread_paused,
    set_cooldown,
    store_message_memory,
)
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _worker_db():
    engine = create_async_engine(get_settings().DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


def _run_async(coro):
    """Run an async coroutine in a sync Celery context."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import threading

            result = {}
            def _runner():
                new_loop = asyncio.new_event_loop()
                try:
                    result["value"] = new_loop.run_until_complete(coro)
                except Exception as exc:
                    result["error"] = exc
                finally:
                    new_loop.close()
            t = threading.Thread(target=_runner)
            t.start()
            t.join()
            if "error" in result:
                raise result["error"]
            return result.get("value")
    except RuntimeError:
        pass
    return asyncio.run(coro)


@celery_app.task(name="app.worker.tasks.instagram_messenger.poll_instagram_messenger")
def poll_instagram_messenger() -> dict:
    """Poll Instagram DM conversations and send AI auto-replies."""
    return _run_async(_poll_instagram_messenger_async())


async def _poll_instagram_messenger_async() -> dict:
    """Async implementation of the Instagram DM poller."""
    stats = {"accounts_checked": 0, "replies_sent": 0, "errors": 0, "skipped_no_token": 0}
    settings = get_settings()

    cf_token = getattr(settings, "CLOUDFLARE_API_TOKEN", "")
    cf_account = getattr(settings, "CLOUDFLARE_ACCOUNT_ID", "")
    dmr_url = getattr(settings, "DMR_BASE_URL", "http://host.docker.internal:12434")

    async with _worker_db() as db:
        result = await db.execute(
            select(SocialAccount).where(
                SocialAccount.platform == "instagram",
                SocialAccount.status == "active",
            )
        )
        accounts = result.scalars().all()

        for account in accounts:
            meta = account.meta_data or {}
            auto_reply = meta.get("instagram_auto_reply", {})
            if not auto_reply.get("enabled", False):
                continue

            # Check for access token — Instagram DMs require API access
            if not account.access_token_enc:
                stats["skipped_no_token"] += 1
                logger.info(
                    "Instagram DM: account %s (%s) has no access token — "
                    "connect via OAuth with instagram_business_manage_messages scope. Skipping.",
                    account.id, account.display_name,
                )
                continue

            stats["accounts_checked"] += 1
            seen = meta.get("instagram_messenger_seen", {})

            try:
                replies = await _process_account(
                    account, auto_reply, seen,
                    cf_token, cf_account, dmr_url,
                )
                stats["replies_sent"] += replies

                meta["instagram_messenger_seen"] = seen
                meta["instagram_messenger_last_checked"] = datetime.now(UTC).isoformat()
                account.meta_data = meta
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(account, "meta_data")
                await db.commit()
            except Exception as exc:
                stats["errors"] += 1
                logger.error(
                    "Instagram DM poll failed for account %s: %s",
                    account.id, exc, exc_info=True,
                )

    logger.info("Instagram DM poll complete: %s", stats)
    return stats


async def _process_account(
    account: SocialAccount,
    config: dict,
    seen: dict,
    cf_token: str,
    cf_account: str,
    dmr_url: str,
) -> int:
    """Process a single Instagram account — poll DMs and reply.

    Uses the Instagram Graph API for messaging (same as Messenger Platform API).
    Endpoint: GET /{ig-user-id}/conversations with platform=instagram
    """
    import httpx

    token = decrypt_token(account.access_token_enc)
    ig_user_id = account.account_id or ""
    replies_sent = 0
    account_name = account.display_name or account.username or "us"

    if not ig_user_id:
        logger.warning("Instagram account %s has no account_id (IG user ID)", account.id)
        return 0

    base_url = "https://graph.facebook.com/v21.0"

    # 1. Fetch conversations
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(
                f"{base_url}/{ig_user_id}/conversations",
                params={
                    "platform": "instagram",
                    "access_token": token,
                    "fields": "id,snippet,unread_count,participants,updated_time",
                },
            )
            if resp.status_code >= 400:
                logger.warning(
                    "Instagram DM API error for account %s: %d %s",
                    account.id, resp.status_code, resp.text[:200],
                )
                return 0

            data = resp.json()
        except Exception as exc:
            logger.warning("Instagram DM fetch failed for account %s: %s", account.id, exc)
            return 0

    conversations = data.get("data", [])
    if not conversations:
        return 0

    for convo in conversations[:20]:
        convo_id = convo.get("id", "")
        if not convo_id:
            continue

        # Skip if no unread messages
        unread = convo.get("unread_count", 0)
        if unread == 0:
            continue

        # Get participant name
        participants = convo.get("participants", {}).get("data", [])
        convo_name = participants[0].get("name", "Unknown") if participants else "Unknown"

        try:
            # 2. Read messages in this conversation
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    f"{base_url}/{convo_id}/messages",
                    params={
                        "access_token": token,
                        "fields": "id,from,created_time,message",
                    },
                )
                if resp.status_code >= 400:
                    logger.warning(
                        "Instagram DM read error for conversation %s: %d",
                        convo_id, resp.status_code,
                    )
                    continue

                msgs_data = resp.json()

            messages = msgs_data.get("data", [])
            if not messages:
                continue

            # 3. Find last inbound message (not from us)
            last_inbound = None
            for msg in reversed(messages):
                sender = msg.get("from", {})
                sender_id = sender.get("id", "") if isinstance(sender, dict) else str(sender)
                if sender_id != ig_user_id and msg.get("message"):
                    last_inbound = msg
                    break

            if not last_inbound:
                continue

            text = (last_inbound.get("message") or "").strip()
            if not text:
                continue

            # 4. Check if already replied
            seen_key = str(convo_id)
            if seen.get(seen_key, "") == text:
                continue

            # 5. Check cooldown
            cooldown_seconds = config.get("cooldown_seconds", 300)
            if not await check_cooldown(account.id, seen_key, cooldown_seconds):
                continue

            # 6. Check human handoff
            if await is_thread_paused(account.id, seen_key):
                continue

            # 7. Generate AI reply
            reply_text = await generate_contextual_reply(
                config, text, account_name,
                account.id, seen_key,
                cf_token, cf_account, dmr_url,
            )

            if not reply_text:
                reply_text = config.get("fallback_text", "Thanks for your message! I'll get back to you soon.")

            # 8. Send reply via Instagram Messaging API
            recipient_id = ""
            if participants:
                recipient_id = participants[0].get("id", "")

            if not recipient_id:
                logger.warning("Instagram DM: no recipient_id for conversation %s", convo_id)
                continue

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{base_url}/{ig_user_id}/messages",
                    params={"access_token": token},
                    json={
                        "recipient": {"id": recipient_id},
                        "message": {"text": reply_text},
                    },
                )
                if resp.status_code >= 400:
                    logger.warning(
                        "Instagram DM send error for conversation %s: %d %s",
                        convo_id, resp.status_code, resp.text[:200],
                    )
                    continue

            # 9. Store in memory
            await store_message_memory(
                account.team_id, account.id, seen_key,
                "them", text,
            )
            await store_message_memory(
                account.team_id, account.id, seen_key,
                "me", reply_text,
            )

            # 10. Mark seen + cooldown
            seen[seen_key] = text
            await set_cooldown(account.id, seen_key, cooldown_seconds)
            replies_sent += 1
            logger.info(
                "Instagram auto-reply sent to '%s' (conversation %s) for account %s",
                convo_name, convo_id, account.id,
            )

            await asyncio.sleep(3)

        except Exception as exc:
            logger.error(
                "Error processing Instagram conversation %s: %s",
                convo_id, exc, exc_info=True,
            )

    return replies_sent
