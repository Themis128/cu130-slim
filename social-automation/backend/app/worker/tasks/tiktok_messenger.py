"""Celery task — TikTok DM chatbot via Business Messaging API.

TikTok's Business Messaging API is in Open Beta (APAC, LATAM, METAP, NA).
Not yet available in EU/Greece. Only for inbound messages (users must
message first). 48-hour reply window, 10 messages per window.

This task polls for new DM conversations and sends AI auto-replies when
the API becomes available in the account's region.

Flow:
    1. Find TikTok accounts with auto-reply enabled
    2. For each account, fetch recent conversations via Business Messaging API
    3. For each conversation with new messages:
       a. Read messages
       b. Find last inbound message
       c. Check if already replied (seen tracking)
       d. Check per-conversation cooldown (Redis)
       e. Generate AI reply (Cloudflare Workers AI)
       f. Send reply via API
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
from app.core.security import decrypt_token
from app.models.social_account import SocialAccount
from app.services.messenger_chatbot import (
    check_cooldown,
    generate_contextual_reply,
    is_thread_paused,
    set_cooldown,
    store_message_memory,
)
from app.services.tiktok_api import TikTokAPIClient, TikTokAPIError
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
    """Poll TikTok DM conversations and send AI auto-replies."""
    return _run_async(_poll_tiktok_messenger_async())


async def _poll_tiktok_messenger_async() -> dict:
    """Async implementation of the TikTok DM poller."""
    stats = {"accounts_checked": 0, "replies_sent": 0, "errors": 0}
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

            # Check for access token
            if not account.access_token_enc:
                logger.info(
                    "TikTok DM: account %s has no access token — "
                    "Business Messaging API not available in EU yet. Skipping.",
                    account.id,
                )
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
    """Process a single TikTok account — poll DMs and reply."""
    token = decrypt_token(account.access_token_enc)
    open_id = (account.meta_data or {}).get("open_id", account.account_id or "")
    client = TikTokAPIClient(access_token=token, open_id=open_id)
    replies_sent = 0
    account_name = account.display_name or account.username or "us"

    # 1. Fetch conversations
    try:
        convos_result = await client.list_dm_conversations()
    except TikTokAPIError as exc:
        logger.warning("TikTok DM API error for account %s: %s", account.id, exc)
        return 0

    conversations = convos_result.get("data", {}).get("conversations", [])
    if not conversations:
        return 0

    for convo in conversations[:20]:
        convo_id = convo.get("conversation_id", "")
        convo_name = convo.get("name", convo.get("participant_name", "Unknown"))

        if not convo_id:
            continue

        try:
            # 2. Read messages
            msgs_result = await client.get_dm_messages(convo_id)
            messages = msgs_result.get("data", {}).get("messages", [])
            if not messages:
                continue

            # 3. Find last inbound message
            last_inbound = None
            for msg in reversed(messages):
                sender = msg.get("sender", {})
                sender_id = sender.get("open_id", "") if isinstance(sender, dict) else str(sender)
                if sender_id != open_id and msg.get("content", {}).get("text"):
                    last_inbound = msg
                    break

            if not last_inbound:
                continue

            # 4. Check if already replied
            text = last_inbound.get("content", {}).get("text", "").strip()
            if not text:
                continue

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

            # 8. Send reply (48-hour window, 10 messages max)
            await client.send_dm(convo_id, {"text": reply_text})

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

        except TikTokAPIError as exc:
            logger.warning("TikTok DM API error in conversation %s: %s", convo_id, exc)
            if exc.status_code == 429:
                logger.warning("TikTok rate-limited — stopping for account %s", account.id)
                break
        except Exception as exc:
            logger.error("Error processing TikTok conversation %s: %s", convo_id, exc, exc_info=True)

    return replies_sent
