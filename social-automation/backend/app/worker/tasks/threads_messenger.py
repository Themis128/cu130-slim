"""Celery task — Threads DM chatbot via browser bridge.

Threads launched DMs in July 2025 but has no public DM API. This task polls
the browser bridge for new messages and sends AI-generated replies.

Flow:
    1. Find Threads accounts with auto-reply enabled
    2. For each account, fetch recent conversations via browser bridge
    3. For each conversation with unread messages:
       a. Read recent messages
       b. Find last inbound message
       c. Check if already replied (seen tracking)
       d. Check per-conversation cooldown (Redis)
       e. Generate AI reply (Cloudflare Workers AI)
       f. Send reply via browser bridge
       g. Mark as seen + set cooldown

State tracking:
    ``meta_data.threads_auto_reply`` stores:
        - enabled, system_prompt, model, fallback_text, max_tokens
        - cooldown_seconds, temperature
    ``meta_data.threads_messenger_seen`` stores:
        - {thread_id: last_replied_message_text}

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
from app.models.social_account import SocialAccount
from app.services.browser_bridge import BrowserBridgeClient, BrowserBridgeError
from app.services.browser_orchestrator import browser_session
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


@celery_app.task(name="app.worker.tasks.threads_messenger.poll_threads_messenger")
def poll_threads_messenger() -> dict:
    """Poll Threads DM conversations and send AI auto-replies."""
    return _run_async(_poll_threads_messenger_async())


async def _poll_threads_messenger_async() -> dict:
    """Async implementation of the Threads DM poller."""
    stats = {"accounts_checked": 0, "replies_sent": 0, "errors": 0}
    settings = get_settings()

    cf_token = getattr(settings, "CLOUDFLARE_API_TOKEN", "")
    cf_account = getattr(settings, "CLOUDFLARE_ACCOUNT_ID", "")
    dmr_url = getattr(settings, "DMR_BASE_URL", "http://host.docker.internal:12434")
    bridge_url = "http://browser-novnc:9223"

    async with _worker_db() as db:
        # Find Threads accounts with auto-reply enabled
        result = await db.execute(
            select(SocialAccount).where(
                SocialAccount.platform == "threads",
                SocialAccount.status == "active",
            )
        )
        accounts = result.scalars().all()

        for account in accounts:
            meta = account.meta_data or {}
            auto_reply = meta.get("threads_auto_reply", {})
            if not auto_reply.get("enabled", False):
                continue

            stats["accounts_checked"] += 1
            seen = meta.get("threads_messenger_seen", {})

            try:
                replies = await _process_account(
                    account, auto_reply, seen, bridge_url,
                    cf_token, cf_account, dmr_url,
                )
                stats["replies_sent"] += replies

                # Persist seen state
                meta["threads_messenger_seen"] = seen
                meta["threads_messenger_last_checked"] = datetime.now(UTC).isoformat()
                account.meta_data = meta
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(account, "meta_data")
                await db.commit()
            except Exception as exc:
                stats["errors"] += 1
                logger.error(
                    "Threads DM poll failed for account %s: %s",
                    account.id, exc, exc_info=True,
                )

    logger.info("Threads DM poll complete: %s", stats)
    return stats


async def _process_account(
    account: SocialAccount,
    config: dict,
    seen: dict,
    bridge_url: str,
    cf_token: str,
    cf_account: str,
    dmr_url: str,
) -> int:
    """Process a single Threads account — poll conversations and reply."""
    bridge = BrowserBridgeClient(bridge_url)
    replies_sent = 0
    account_name = account.display_name or account.username or "us"

    # 0. Check browser session
    try:
        session = await bridge.ensure_session("threads")
        if session.get("status") != "active":
            logger.info(
                "Threads DM: browser session not active for account %s — %s",
                account.id, session.get("message", "?"),
            )
            return 0
    except Exception as exc:
        logger.warning("Browser session check failed for account %s: %s", account.id, exc)
        return 0

    # 1. Fetch conversations
    try:
        async with browser_session("threads", bridge) as b:
            convos_result = await b.get_threads_dm_conversations()
    except (BrowserBridgeError, Exception) as exc:
        logger.warning("Browser bridge error for account %s: %s", account.id, exc)
        return 0

    conversations = convos_result.get("conversations", [])
    # Only process conversations with a thread_id
    threadable = [c for c in conversations if c.get("thread_id")]

    for convo in threadable[:20]:
        thread_id = convo["thread_id"]
        convo_name = convo.get("name", "Unknown")

        try:
            # 2. Read recent messages
            async with browser_session("threads", bridge) as b:
                msgs_result = await b.get_threads_dm_messages(thread_id)
            messages = msgs_result.get("messages", [])
            if not messages:
                continue

            # 3. Find the last inbound message (not from us)
            last_inbound = None
            for msg in reversed(messages):
                sender = msg.get("sender", "").lower()
                if account_name.lower() not in sender and sender != "me" and msg.get("text"):
                    last_inbound = msg
                    break

            if not last_inbound:
                continue

            # 4. Check if we already replied to this message
            last_text = last_inbound["text"].strip()
            seen_key = str(thread_id)
            last_seen = seen.get(seen_key, "")

            if last_seen == last_text:
                continue  # Already replied

            # 5. Check per-conversation cooldown (Redis)
            cooldown_seconds = config.get("cooldown_seconds", 300)
            if not await check_cooldown(account.id, thread_id, cooldown_seconds):
                continue  # Cooldown active, skip

            # 6. Check human handoff (paused thread)
            if await is_thread_paused(account.id, thread_id):
                logger.debug("Thread %s paused (human handoff), skipping", thread_id)
                continue

            # 7. Detect intent and retrieve brand context (RAG)
            intent = await detect_intent(last_text, cf_token, cf_account, dmr_url)
            brand_context = await retrieve_brand_context(last_text)

            # 8. Generate AI reply (CF Workers AI)
            reply_text = await generate_contextual_reply(
                config, last_text, account_name,
                account.id, thread_id,
                cf_token, cf_account, dmr_url,
                intent=intent,
                brand_context=brand_context, team_id=account.team_id,
            )

            if not reply_text:
                reply_text = config.get("fallback_text", "Thanks for your message! I'll get back to you soon.")

            # 8. Send the reply
            async with browser_session("threads", bridge) as b:
                await b.send_threads_dm_message(thread_id, reply_text)

            # 9. Store both messages in conversation memory (ChromaDB)
            await store_message_memory(
                account.team_id, account.id, thread_id,
                "them", last_text,
            )
            await store_message_memory(
                account.team_id, account.id, thread_id,
                "me", reply_text,
            )

            # 10. Mark as seen + set cooldown
            seen[seen_key] = last_text
            await set_cooldown(account.id, thread_id, cooldown_seconds)
            replies_sent += 1
            logger.info(
                "Threads auto-reply sent to '%s' (thread %s) for account %s",
                convo_name, thread_id, account.id,
            )

            # Be gentle — wait between replies
            await asyncio.sleep(3)

        except BrowserBridgeError as exc:
            logger.warning(
                "Browser bridge error in thread %s: %s", thread_id, exc
            )
        except Exception as exc:
            logger.error(
                "Error processing Threads thread %s: %s", thread_id, exc, exc_info=True
            )

    return replies_sent
