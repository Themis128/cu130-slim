"""Celery task — personal Messenger chatbot via browser bridge.

Personal Facebook Messenger has no webhook support (Meta only provides
the Messenger Platform API for Pages). This task polls the browser bridge
for new messages in personal conversations and sends AI-generated replies.

Full chatbot features:
    - Conversation memory (last 10 messages per thread, ChromaDB)
    - Brand knowledge RAG (Cloudless.gr brand DNA indexed in ChromaDB)
    - Intent detection (business, personal, question, spam, greeting)
    - Per-conversation config (custom prompts, model, temperature)
    - Human handoff (pause bot for specific threads)
    - Per-conversation cooldown (Redis, 5 min between replies)
    - Language-aware routing (Greek→CF, English→DMR, fallback chain)
    - Unread-only processing (skip conversations already read on mobile)

Flow:
    1. Find Facebook personal (user) accounts with auto-reply enabled
    2. For each account, fetch recent conversations via browser bridge
    3. For each conversation with thread_id AND unread=True:
       a. Read recent messages
       b. Find last inbound message ("them")
       c. Check if already replied (seen tracking)
       d. Check per-conversation cooldown (Redis)
       e. Check human handoff pause
       f. Detect intent (DMR first, CF fallback)
       g. Retrieve brand context (RAG) and conversation memory
       h. Generate context-aware AI reply (language-aware routing)
       i. Send typing indicator (natural delay)
       j. Send reply via browser bridge
       k. Store both messages in conversation memory (ChromaDB)
       l. Mark as seen + set cooldown

State tracking:
    ``meta_data.personal_messenger_auto_reply`` stores:
        - enabled, system_prompt, model, fallback_text, max_tokens
        - last_checked: ISO timestamp of last poll
        - cooldown_seconds: per-conversation cooldown (default 300)
    ``meta_data.personal_messenger_seen`` stores:
        - {thread_id: last_replied_message_text}

The task runs every 2 minutes via Celery beat. It is non-fatal — a single
account or conversation failure does not abort the loop.
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
            # We're already in an async context — create a new loop
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


@celery_app.task(name="app.worker.tasks.personal_messenger.poll_personal_messenger")
def poll_personal_messenger() -> dict:
    """Poll personal Messenger conversations and send AI auto-replies."""
    return _run_async(_poll_personal_messenger_async())


async def _poll_personal_messenger_async() -> dict:
    """Async implementation of the personal Messenger poller."""
    stats = {"accounts_checked": 0, "replies_sent": 0, "errors": 0}
    settings = get_settings()

    # AI config from env
    cf_token = settings.CLOUDFLARE_API_TOKEN if hasattr(settings, "CLOUDFLARE_API_TOKEN") else ""
    cf_account = settings.CLOUDFLARE_ACCOUNT_ID if hasattr(settings, "CLOUDFLARE_ACCOUNT_ID") else ""
    dmr_url = getattr(settings, "DMR_BASE_URL", "http://host.docker.internal:12434")
    browser_bridge_url = "http://browser-novnc:9223"

    async with _worker_db() as db:
        # Find Facebook personal accounts with auto-reply enabled
        result = await db.execute(
            select(SocialAccount).where(
                SocialAccount.platform == "facebook",
                SocialAccount.account_type == "user",
                SocialAccount.status == "active",
            )
        )
        accounts = result.scalars().all()

        for account in accounts:
            meta = account.meta_data or {}
            auto_reply = meta.get("personal_messenger_auto_reply", {})
            if not auto_reply.get("enabled", False):
                continue

            stats["accounts_checked"] += 1
            seen = meta.get("personal_messenger_seen", {})

            try:
                replies = await _process_account(
                    account, auto_reply, seen, browser_bridge_url,
                    cf_token, cf_account, dmr_url,
                )
                stats["replies_sent"] += replies

                # Update last_checked timestamp and persist seen state
                meta["personal_messenger_seen"] = seen
                meta["personal_messenger_last_checked"] = datetime.now(UTC).isoformat()
                account.meta_data = meta
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(account, "meta_data")
                await db.commit()
            except Exception as exc:
                stats["errors"] += 1
                logger.error(
                    "Personal Messenger poll failed for account %s: %s",
                    account.id, exc, exc_info=True,
                )

    logger.info("Personal Messenger poll complete: %s", stats)
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
    """Process a single personal account — poll conversations and reply."""
    from app.services.browser_bridge import BrowserBridgeClient, BrowserBridgeError

    bridge = BrowserBridgeClient(bridge_url)
    replies_sent = 0
    account_name = account.display_name or account.username or "us"

    # 0. Check browser session and auto-restart if needed
    try:
        session = await bridge.ensure_session("facebook")
        if session["status"] != "active":
            logger.info(
                "Personal Messenger: browser session not active for account %s — %s",
                account.id, session["message"],
            )
            return 0
    except Exception as exc:
        logger.warning("Browser session check failed for account %s: %s", account.id, exc)
        return 0

    # 1. Fetch conversations (use fast mobile-basic read first, fallback to full SPA)
    try:
        convos_result = await bridge.get_personal_messenger_conversations_fast()
    except (BrowserBridgeError, Exception) as exc:
        logger.warning("Browser bridge error for account %s: %s", account.id, exc)
        return 0

    conversations = convos_result.get("conversations", [])
    # Process all conversations with a thread_id (both regular and E2EE)
    # Only reply to conversations marked as unread by Facebook — this
    # prevents the bot from replying to messages you've already read on
    # your phone (Android/iOS) or another device.
    threadable = [c for c in conversations if c.get("thread_id") and c.get("unread", False)]

    for convo in threadable[:20]:  # Increased from 10 to 20 conversations per poll
        thread_id = convo["thread_id"]
        convo_name = convo.get("name", "Unknown")
        is_e2ee = convo.get("e2ee", False)

        try:
            # 2. Read recent messages (fast mobile-basic first, fallback to SPA)
            msgs_result = await bridge.get_personal_messenger_messages_fast(thread_id, is_e2ee=is_e2ee)
            messages = msgs_result.get("messages", [])
            if not messages:
                continue

            # 3. Find the last inbound message
            last_inbound = None
            for msg in reversed(messages):
                if msg.get("sender") == "them" and msg.get("text"):
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

            # 7. Detect intent (DMR first, CF fallback)
            intent = await detect_intent(
                last_text, cf_token, cf_account, dmr_url,
            )

            # 8. Retrieve brand context (RAG)
            brand_context = await retrieve_brand_context(last_text)

            # 9. Generate context-aware reply (DMR first, CF fallback)
            reply_text = await generate_contextual_reply(
                config, last_text, account_name,
                account.id, thread_id,
                cf_token, cf_account, dmr_url,
                intent=intent,
                brand_context=brand_context,
            )

            # 10. Typing indicator — pause to feel natural (Meta best practice:
            # "Be Predictable" — users expect a brief pause before a reply)
            # Scale delay with reply length: ~1s per 50 chars, capped at 5s
            typing_delay = min(max(len(reply_text) / 50, 1.5), 5.0)
            try:
                await bridge.trigger_typing_indicator(thread_id, is_e2ee=is_e2ee, duration=typing_delay)
            except Exception:
                pass  # Non-fatal — typing indicator is a nice-to-have

            # 11. Send the reply (pass is_e2ee for correct URL)
            await bridge.send_personal_messenger_message(thread_id, reply_text, is_e2ee=is_e2ee)

            # 12. Store both messages in conversation memory (ChromaDB)
            await store_message_memory(
                account.team_id, account.id, thread_id,
                "them", last_text,
            )
            await store_message_memory(
                account.team_id, account.id, thread_id,
                "me", reply_text,
            )

            # 13. Mark as seen + set cooldown
            seen[seen_key] = last_text
            await set_cooldown(account.id, thread_id, cooldown_seconds)
            replies_sent += 1
            logger.info(
                "Personal auto-reply sent to '%s' (thread %s, e2ee=%s, intent=%s) for account %s",
                convo_name, thread_id, is_e2ee, intent, account.id,
            )

            # Be gentle — wait between replies
            await asyncio.sleep(3)

        except BrowserBridgeError as exc:
            logger.warning(
                "Browser bridge error in thread %s: %s", thread_id, exc
            )
        except Exception as exc:
            logger.error(
                "Error processing thread %s: %s", thread_id, exc, exc_info=True
            )

    return replies_sent
