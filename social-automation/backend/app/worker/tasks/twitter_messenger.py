"""Celery task — Twitter/X DM chatbot via official API v2.

Twitter/X has a full DM API (v2) with endpoints for sending and receiving.
Requires OAuth 2.0 user-context with dm.read + dm.write scopes.
Free tier: read-only. Basic ($200/mo): 1 send/24h. Pro: 15 sends/15min.

Flow:
    1. Find Twitter accounts with auto-reply enabled
    2. For each account, fetch recent DM events via API
    3. For each inbound message:
       a. Check if already replied (seen tracking)
       b. Check per-conversation cooldown (Redis)
       c. Generate AI reply (Cloudflare Workers AI)
       d. Send reply via API
       e. Mark as seen + set cooldown

State tracking:
    ``meta_data.twitter_auto_reply`` stores:
        - enabled, system_prompt, model, fallback_text, max_tokens
        - cooldown_seconds, temperature
    ``meta_data.twitter_messenger_seen`` stores:
        - {conversation_id: last_replied_message_text}

The task runs every 5 minutes via Celery beat (slower than others due to
Twitter's strict rate limits).
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
from app.services.twitter_api import TwitterAPIClient, TwitterAPIError
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


@celery_app.task(name="app.worker.tasks.twitter_messenger.poll_twitter_messenger")
def poll_twitter_messenger() -> dict:
    """Poll Twitter DM events and send AI auto-replies."""
    return _run_async(_poll_twitter_messenger_async())


async def _poll_twitter_messenger_async() -> dict:
    """Async implementation of the Twitter DM poller."""
    stats = {"accounts_checked": 0, "replies_sent": 0, "errors": 0}
    settings = get_settings()

    cf_token = getattr(settings, "CLOUDFLARE_API_TOKEN", "")
    cf_account = getattr(settings, "CLOUDFLARE_ACCOUNT_ID", "")
    dmr_url = getattr(settings, "DMR_BASE_URL", "http://host.docker.internal:12434")

    async with _worker_db() as db:
        # Find Twitter accounts with auto-reply enabled
        result = await db.execute(
            select(SocialAccount).where(
                SocialAccount.platform == "twitter",
                SocialAccount.status == "active",
            )
        )
        accounts = result.scalars().all()

        for account in accounts:
            meta = account.meta_data or {}
            auto_reply = meta.get("twitter_auto_reply", {})
            if not auto_reply.get("enabled", False):
                continue

            # Check for access token
            if not account.access_token_enc:
                logger.info(
                    "Twitter DM: account %s has no access token — skipping",
                    account.id,
                )
                continue

            stats["accounts_checked"] += 1
            seen = meta.get("twitter_messenger_seen", {})

            try:
                replies = await _process_account(
                    account, auto_reply, seen,
                    cf_token, cf_account, dmr_url,
                )
                stats["replies_sent"] += replies

                # Persist seen state
                meta["twitter_messenger_seen"] = seen
                meta["twitter_messenger_last_checked"] = datetime.now(UTC).isoformat()
                account.meta_data = meta
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(account, "meta_data")
                await db.commit()
            except Exception as exc:
                stats["errors"] += 1
                logger.error(
                    "Twitter DM poll failed for account %s: %s",
                    account.id, exc, exc_info=True,
                )

    logger.info("Twitter DM poll complete: %s", stats)
    return stats


async def _process_account(
    account: SocialAccount,
    config: dict,
    seen: dict,
    cf_token: str,
    cf_account: str,
    dmr_url: str,
) -> int:
    """Process a single Twitter account — poll DMs and reply."""
    token = decrypt_token(account.access_token_enc)
    client = TwitterAPIClient(access_token=token)
    replies_sent = 0
    account_name = account.display_name or account.username or "us"
    our_user_id = account.account_id  # Twitter user ID

    # 1. Fetch recent DM events
    try:
        events_result = await client.list_dm_events(max_results=50)
    except TwitterAPIError as exc:
        logger.warning("Twitter DM API error for account %s: %s", account.id, exc)
        return 0

    events = events_result.get("data", [])
    if not events:
        return 0

    # 2. Group events by conversation and find inbound messages
    for event in events:
        event_id = event.get("id", "")
        sender_id = event.get("sender_id", "")
        text = event.get("text", "")
        conversation_id = event.get("dm_conversation_id", "")

        # Skip our own messages
        if sender_id == our_user_id:
            continue

        # Skip empty messages
        if not text or not text.strip():
            continue

        # Skip if already replied to this message
        seen_key = str(conversation_id or event_id)
        last_seen = seen.get(seen_key, "")
        if last_seen == text.strip():
            continue

        # Check per-conversation cooldown (Redis)
        cooldown_seconds = config.get("cooldown_seconds", 300)
        if not await check_cooldown(account.id, seen_key, cooldown_seconds):
            continue  # Cooldown active, skip

        # Check human handoff (paused thread)
        if await is_thread_paused(account.id, seen_key):
            logger.debug("Thread %s paused (human handoff), skipping", seen_key)
            continue

        try:
            # 3. Generate AI reply (CF Workers AI)
            reply_text = await generate_contextual_reply(
                config, text.strip(), account_name,
                account.id, seen_key,
                cf_token, cf_account, dmr_url,
            )

            if not reply_text:
                reply_text = config.get("fallback_text", "Thanks for your message! I'll get back to you soon.")

            # 4. Send the reply
            if conversation_id:
                await client.send_dm_to_conversation(conversation_id, reply_text)
            else:
                await client.send_dm(sender_id, reply_text)

            # 5. Store both messages in conversation memory (ChromaDB)
            await store_message_memory(
                account.team_id, account.id, seen_key,
                "them", text.strip(),
            )
            await store_message_memory(
                account.team_id, account.id, seen_key,
                "me", reply_text,
            )

            # 6. Mark as seen + set cooldown
            seen[seen_key] = text.strip()
            await set_cooldown(account.id, seen_key, cooldown_seconds)
            replies_sent += 1
            logger.info(
                "Twitter auto-reply sent to '%s' (conversation %s) for account %s",
                sender_id, conversation_id, account.id,
            )

            # Be gentle — wait between replies (Twitter rate limits)
            await asyncio.sleep(5)

        except TwitterAPIError as exc:
            logger.warning(
                "Twitter DM API error sending reply to %s: %s", seen_key, exc
            )
            # If rate-limited, stop processing this account
            if exc.status_code == 429:
                logger.warning("Twitter rate-limited — stopping for account %s", account.id)
                break
        except Exception as exc:
            logger.error(
                "Error processing Twitter DM %s: %s", seen_key, exc, exc_info=True
            )

    return replies_sent
