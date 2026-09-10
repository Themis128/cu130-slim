"""Celery task — personal Messenger auto-reply via browser bridge.

Personal Facebook Messenger has no webhook support (Meta only provides
the Messenger Platform API for Pages). This task polls the browser bridge
for new messages in personal conversations and sends AI-generated replies.

Flow:
    1. Find Facebook personal (user) accounts with auto-reply enabled
    2. For each account, fetch recent conversations via browser bridge
    3. For each conversation with a thread_id, read the latest messages
    4. Detect new inbound messages (not yet replied to)
    5. Generate an AI response (Cloudflare Workers AI → DMR → fallback)
    6. Send the reply via browser bridge

State tracking:
    ``meta_data.personal_messenger_auto_reply`` stores:
        - enabled, system_prompt, model, fallback_text, max_tokens
        - last_checked: ISO timestamp of last poll
    ``meta_data.personal_messenger_seen`` stores:
        - {thread_id: last_replied_message_text}

The task runs every 2 minutes via Celery beat. It is non-fatal — a single
account or conversation failure does not abort the loop.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.models.social_account import SocialAccount
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()

logger = logging.getLogger(__name__)

# AI config
DEFAULT_MODEL = "@cf/meta/llama-3.1-8b-instruct"
DMR_MODEL = "ai/qwen3:8b-q4_K_M"


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

    # 1. Fetch conversations
    try:
        convos_result = await bridge.get_personal_messenger_conversations()
    except BrowserBridgeError as exc:
        logger.warning("Browser bridge error for account %s: %s", account.id, exc)
        return 0

    conversations = convos_result.get("conversations", [])
    # Only process conversations with a thread_id (skip E2EE-only ones for now)
    threadable = [c for c in conversations if c.get("thread_id")]

    for convo in threadable[:10]:  # Limit to 10 conversations per poll
        thread_id = convo["thread_id"]
        convo_name = convo.get("name", "Unknown")

        try:
            # 2. Read recent messages
            msgs_result = await bridge.get_personal_messenger_messages(thread_id)
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

            # 5. Generate AI response
            reply_text = await _generate_ai_response(
                config, last_text, account_name,
                cf_token, cf_account, dmr_url,
            )

            # 6. Send the reply
            await bridge.send_personal_messenger_message(thread_id, reply_text)

            # 7. Mark as seen
            seen[seen_key] = last_text
            replies_sent += 1
            logger.info(
                "Personal auto-reply sent to '%s' (thread %s) for account %s",
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
                "Error processing thread %s: %s", thread_id, exc, exc_info=True
            )

    return replies_sent


async def _generate_ai_response(
    config: dict,
    user_message: str,
    account_name: str,
    cf_token: str,
    cf_account: str,
    dmr_url: str,
) -> str:
    """Generate an AI response using Cloudflare Workers AI → DMR → fallback."""
    system_prompt = config.get(
        "system_prompt",
        "You are a helpful assistant for {page_name}. Reply concisely and professionally.",
    ).replace("{page_name}", account_name)
    model = config.get("model", DEFAULT_MODEL)
    max_tokens = config.get("max_tokens", 200)
    fallback = config.get(
        "fallback_text", "Thanks for your message! I'll get back to you soon."
    )

    # Try Cloudflare Workers AI first (free tier)
    if cf_token and cf_account:
        try:
            url = f"https://api.cloudflare.com/client/v4/accounts/{cf_account}/ai/run/{model}"
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {cf_token}"},
                    json={
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_message},
                        ],
                        "max_tokens": max_tokens,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("result") and data["result"].get("response"):
                        return data["result"]["response"].strip()
        except Exception as exc:
            logger.warning("Cloudflare AI failed: %s", exc)

    # Try DMR (local Docker Model Runner) as fallback
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{dmr_url}/engines/v1/chat/completions",
                json={
                    "model": DMR_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    "max_tokens": max_tokens,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                choices = data.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", fallback).strip()
    except Exception as exc:
        logger.warning("DMR AI failed: %s", exc)

    return fallback
