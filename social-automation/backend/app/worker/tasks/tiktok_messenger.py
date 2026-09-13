"""Celery task — TikTok DM chatbot via browser automation.

TikTok's Business Messaging API is in Open Beta (APAC, LATAM, METAP, NA)
but not available in EU/Greece. Only for inbound messages.

This task uses browser automation (same approach as personal Facebook
Messenger, LinkedIn, Threads, and Twitter) to poll for new DMs and send
AI auto-replies through the TikTok web UI (tiktok.com/messages).

Flow:
    1. Find TikTok accounts with auto-reply enabled
    2. For each account, use browser bridge to check session
    3. Poll conversations via browser bridge
    4. For each conversation with new messages:
       a. Read messages via browser bridge
       b. Find last inbound message
       c. Check if already replied (seen tracking)
       d. Check per-conversation cooldown (Redis)
       e. Generate AI reply (Cloudflare Workers AI)
       f. Send reply via browser bridge
       g. Mark as seen + set cooldown

State tracking:
    ``meta_data.tiktok_auto_reply`` stores bot config
    ``meta_data.tiktok_messenger_seen`` stores {conversation_id: last_text}

The task runs every 5 minutes via Celery beat.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.models.social_account import SocialAccount
from app.services.browser_bridge import BrowserBridgeClient
from app.services.messenger_chatbot import (
    check_cooldown,
    detect_intent,
    generate_contextual_reply,
    is_thread_paused,
    retrieve_brand_context,
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


@celery_app.task(name="app.worker.tasks.tiktok_messenger.poll_tiktok_messenger")
def poll_tiktok_messenger() -> dict:
    """Poll TikTok DM conversations and send AI auto-replies via browser bridge."""
    return _run_async(_poll_tiktok_messenger_async())


async def _poll_tiktok_messenger_async() -> dict:
    """Async implementation of the TikTok DM poller."""
    stats = {"accounts_checked": 0, "replies_sent": 0, "errors": 0, "skipped_no_session": 0}
    settings = get_settings()

    cf_token = getattr(settings, "CLOUDFLARE_API_TOKEN", "")
    cf_account = getattr(settings, "CLOUDFLARE_ACCOUNT_ID", "")
    dmr_url = getattr(settings, "DMR_BASE_URL", "http://host.docker.internal:12434")

    async with _worker_db() as db:
        result = await db.execute(
            select(SocialAccount).where(
                SocialAccount.platform == "tiktok",
                SocialAccount.status == "active",
            )
        )
        accounts = result.scalars().all()

        for account in accounts:
            meta = account.meta_data or {}
            auto_reply = meta.get("tiktok_auto_reply", {})
            if not auto_reply.get("enabled", False):
                continue

            stats["accounts_checked"] += 1
            seen = meta.get("tiktok_messenger_seen", {})

            try:
                replies = await _process_account(
                    account, auto_reply, seen,
                    cf_token, cf_account, dmr_url,
                )
                stats["replies_sent"] += replies

                meta["tiktok_messenger_seen"] = seen
                meta["tiktok_messenger_last_checked"] = datetime.now(UTC).isoformat()
                account.meta_data = meta
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(account, "meta_data")
                await db.commit()
            except Exception as exc:
                stats["errors"] += 1
                logger.error(
                    "TikTok DM poll failed for account %s: %s",
                    account.id, exc, exc_info=True,
                )

    logger.info("TikTok DM poll complete: %s", stats)
    return stats


async def _process_account(
    account: SocialAccount,
    config: dict,
    seen: dict,
    cf_token: str,
    cf_account: str,
    dmr_url: str,
) -> int:
    """Process a single TikTok account — poll DMs via browser bridge and reply."""
    bridge = BrowserBridgeClient(get_settings().BROWSER_BRIDGE_URL)
    replies_sent = 0
    account_name = account.display_name or account.username or "us"

    # 0. Check browser bridge session
    try:
        status = await bridge.session_status()
        if not status.get("logged_in") and not status.get("has_session"):
            logger.info(
                "TikTok DM: browser bridge session not active for account %s — "
                "login via noVNC (port 6080) to tiktok.com first. Skipping.",
                account.id,
            )
            return 0
    except Exception as exc:
        logger.warning("TikTok DM: browser bridge check failed for account %s: %s", account.id, exc)
        return 0

    # 1. Fetch conversations via browser bridge
    try:
        convos_result = await bridge.get_tiktok_dm_conversations()
    except Exception as exc:
        logger.warning("TikTok DM: browser bridge error for account %s: %s", account.id, exc)
        return 0

    conversations = convos_result.get("conversations", [])
    if not conversations:
        return 0

    for convo in conversations[:20]:
        convo_id = convo.get("thread_id", "")
        convo_name = convo.get("name", "Unknown")

        if not convo_id:
            continue

        try:
            # 2. Read messages in this conversation
            msgs_result = await bridge.get_tiktok_dm_messages(convo_id)
            messages = msgs_result.get("messages", [])
            if not messages:
                continue

            # 3. Find last inbound message
            last_inbound = None
            for msg in reversed(messages):
                text = msg.get("text", "")
                if text and text.strip():
                    last_inbound = msg
                    break

            if not last_inbound:
                continue

            text = last_inbound.get("text", "").strip()
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

            # 7. Detect intent and retrieve brand context (RAG)
            intent = await detect_intent(text, cf_token, cf_account, dmr_url)
            brand_context = await retrieve_brand_context(text)

            # 8. Generate AI reply
            reply_text = await generate_contextual_reply(
                config, text, account_name,
                account.id, seen_key,
                cf_token, cf_account, dmr_url,
                intent=intent,
                brand_context=brand_context,
            )

            if not reply_text:
                reply_text = config.get("fallback_text", "Thanks for your message! I'll get back to you soon.")

            # 8. Send reply via browser bridge
            await bridge.send_tiktok_dm_message(convo_id, reply_text)

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
                "TikTok auto-reply sent to '%s' (conversation %s) for account %s",
                convo_name, convo_id, account.id,
            )

            await asyncio.sleep(3)

        except Exception as exc:
            logger.error(
                "Error processing TikTok conversation %s: %s",
                convo_id, exc, exc_info=True,
            )

    return replies_sent
