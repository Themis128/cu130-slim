"""Celery task — LinkedIn DM chatbot via browser sidecar.

LinkedIn has no DM API for personal or business accounts. This task polls
the LinkedIn browser sidecar for new messages and sends AI-generated replies.

Flow:
    1. Find LinkedIn accounts (personal + organization) with auto-reply enabled
    2. For each account, fetch recent conversations via sidecar
    3. For each conversation with unread messages:
       a. Read recent messages
       b. Find last inbound message
       c. Check if already replied (seen tracking)
       d. Check per-conversation cooldown (Redis)
       e. Generate AI reply (Cloudflare Workers AI)
       f. Send reply via sidecar
       g. Mark as seen + set cooldown

State tracking:
    ``meta_data.linkedin_auto_reply`` stores:
        - enabled, system_prompt, model, fallback_text, max_tokens
        - cooldown_seconds, temperature
    ``meta_data.linkedin_messenger_seen`` stores:
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
from app.services.linkedin_sidecar import LinkedInSidecarClient, LinkedInSidecarError
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


@celery_app.task(name="app.worker.tasks.linkedin_messenger.poll_linkedin_messenger")
def poll_linkedin_messenger() -> dict:
    """Poll LinkedIn DM conversations and send AI auto-replies."""
    return _run_async(_poll_linkedin_messenger_async())


async def _poll_linkedin_messenger_async() -> dict:
    """Async implementation of the LinkedIn DM poller."""
    stats = {"accounts_checked": 0, "replies_sent": 0, "errors": 0}
    settings = get_settings()

    cf_token = getattr(settings, "CLOUDFLARE_API_TOKEN", "")
    cf_account = getattr(settings, "CLOUDFLARE_ACCOUNT_ID", "")
    dmr_url = getattr(settings, "DMR_BASE_URL", "http://host.docker.internal:12434")
    sidecar_url = "http://linkedin-browser-sidecar:9225"

    async with _worker_db() as db:
        # Find LinkedIn accounts with auto-reply enabled
        result = await db.execute(
            select(SocialAccount).where(
                SocialAccount.platform == "linkedin",
                SocialAccount.status == "active",
            )
        )
        accounts = result.scalars().all()

        for account in accounts:
            meta = account.meta_data or {}
            auto_reply = meta.get("linkedin_auto_reply", {})
            if not auto_reply.get("enabled", False):
                continue

            stats["accounts_checked"] += 1
            seen = meta.get("linkedin_messenger_seen", {})

            try:
                replies = await _process_account(
                    account, auto_reply, seen, sidecar_url,
                    cf_token, cf_account, dmr_url,
                )
                stats["replies_sent"] += replies

                # Persist seen state
                meta["linkedin_messenger_seen"] = seen
                meta["linkedin_messenger_last_checked"] = datetime.now(UTC).isoformat()
                account.meta_data = meta
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(account, "meta_data")
                await db.commit()
            except Exception as exc:
                stats["errors"] += 1
                logger.error(
                    "LinkedIn DM poll failed for account %s: %s",
                    account.id, exc, exc_info=True,
                )

    logger.info("LinkedIn DM poll complete: %s", stats)
    return stats


async def _process_account(
    account: SocialAccount,
    config: dict,
    seen: dict,
    sidecar_url: str,
    cf_token: str,
    cf_account: str,
    dmr_url: str,
) -> int:
    """Process a single LinkedIn account — poll conversations and reply."""
    sidecar = LinkedInSidecarClient(sidecar_url)
    replies_sent = 0
    account_name = account.display_name or account.username or "us"

    # Skip while LinkedIn rate-limit cooldown is active
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(get_settings().REDIS_URL, decode_responses=True)
        cooling = await r.get(f"linkedin:dm:ratelimit:{account.id}")
        await r.aclose()
        if cooling:
            logger.info(
                "LinkedIn DM: skipping account %s (rate-limit cooldown)",
                account.id,
            )
            return 0
    except Exception:
        pass

    # 0. Check sidecar session / global rate-limit circuit
    try:
        health = await sidecar.health()
        if health.get("rate_limited"):
            logger.info(
                "LinkedIn DM: sidecar circuit open until %s — skipping account %s",
                health.get("rate_limit_until"),
                account.id,
            )
            return 0
        if not health.get("has_session"):
            logger.info(
                "LinkedIn DM: sidecar session not active for account %s",
                account.id,
            )
            return 0
    except Exception as exc:
        logger.warning("LinkedIn sidecar health check failed for account %s: %s", account.id, exc)
        return 0

    # 1. Fetch conversations (LinkedIn aggressively rate-limits browser polling)
    try:
        convos_result = await sidecar.get_conversations()
    except LinkedInSidecarError as exc:
        if exc.status_code == 429 or "RATE_LIMITED" in str(exc.detail):
            logger.warning(
                "LinkedIn rate-limited while listing DMs for account %s — backing off",
                account.id,
            )
            try:
                import redis.asyncio as aioredis

                r = aioredis.from_url(get_settings().REDIS_URL, decode_responses=True)
                await r.setex(f"linkedin:dm:ratelimit:{account.id}", 21600, "1")  # 6h
                await r.aclose()
            except Exception:
                pass
            return 0
        logger.warning("LinkedIn sidecar error for account %s: %s", account.id, exc)
        return 0
    except Exception as exc:
        logger.warning("LinkedIn sidecar error for account %s: %s", account.id, exc)
        return 0

    conversations = convos_result.get("conversations", [])
    # Only process conversations with a thread_id
    threadable = [c for c in conversations if c.get("thread_id")]

    for convo in threadable[:20]:
        thread_id = convo["thread_id"]
        convo_name = convo.get("name", "Unknown")

        try:
            # 2. Read recent messages
            msgs_result = await sidecar.get_thread_messages(thread_id)
            messages = msgs_result.get("messages", [])
            if not messages:
                continue

            # 3. Find the last inbound message (not from us)
            last_inbound = None
            for msg in reversed(messages):
                sender = msg.get("sender", "").lower()
                # Skip our own messages (account name or "me")
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
                brand_context=brand_context,
            )

            if not reply_text:
                reply_text = config.get("fallback_text", "Thanks for your message! I'll get back to you soon.")

            # 8. Send the reply
            await sidecar.send_message(thread_id, reply_text)

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
                "LinkedIn auto-reply sent to '%s' (thread %s) for account %s",
                convo_name, thread_id, account.id,
            )

            # Be gentle — wait between replies
            await asyncio.sleep(3)

        except LinkedInSidecarError as exc:
            logger.warning(
                "LinkedIn sidecar error in thread %s: %s", thread_id, exc
            )
        except Exception as exc:
            logger.error(
                "Error processing LinkedIn thread %s: %s", thread_id, exc, exc_info=True
            )

    return replies_sent
