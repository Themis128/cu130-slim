"""Celery task — Instagram DM chatbot via Instagram Messaging API.

Instagram uses the same Messenger Platform API as Facebook Pages for DMs.
Requires the `instagram_business_manage_messages` permission and a
connected Instagram Business/Creator account with an access token.

For personal Instagram accounts without API access, this task gracefully
skips (no token = no API calls).

Flow:
    1. Find Instagram accounts with auto-reply enabled
    2. For each account with an access token:
       a. Fetch recent conversations via InstagramAPIClient
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
from app.services.browser_bridge import BrowserBridgeClient
from app.services.instagram_api import InstagramAPIClient, InstagramAPIError
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
                    db, account, auto_reply, seen,
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
    db: AsyncSession,
    account: SocialAccount,
    config: dict,
    seen: dict,
    cf_token: str,
    cf_account: str,
    dmr_url: str,
) -> int:
    """Process a single Instagram account — poll DMs and reply.

    Uses InstagramAPIClient (Instagram Messaging API / Graph API v23.0).
    """
    meta = account.meta_data or {}
    ig_user_id = account.account_id or ""
    replies_sent = 0
    account_name = account.display_name or account.username or "us"

    # Try Graph API first (business/creator accounts with App Review)
    client = None
    try:
        token = decrypt_token(account.access_token_enc)
        graph_client = InstagramAPIClient(access_token=token, ig_user_id=ig_user_id)
        # Probe with a 1-conversation fetch to detect capability errors
        await graph_client.get_conversations(limit=1)
        client = graph_client
    except Exception as exc:
        logger.info(
            "Instagram Graph API unavailable for %s: %s — trying web API fallback",
            account.id, exc,
        )
        client = None

    # Fall back to browser bridge (uses logged-in Instagram web session)
    bridge = None
    if client is None:
        settings = get_settings()
        bridge_url = getattr(settings, "BROWSER_BRIDGE_URL", "http://browser-novnc:9223")
        bridge = BrowserBridgeClient(base_url=bridge_url)
        try:
            await bridge.ensure_session("instagram")
            logger.info("Instagram DM: using browser bridge fallback for %s", account.id)
        except Exception as exc:
            logger.warning(
                "Instagram DM: browser bridge unavailable for %s: %s. "
                "Login via noVNC (http://localhost:6080) to enable DM bot.",
                account.id, exc,
            )
            return 0

    # 1. Fetch conversations
    try:
        if client is not None:
            convos_result = await client.get_conversations(limit=25)
        elif bridge is not None:
            bridge_result = await bridge.get_instagram_dm_conversations()
            if "error" in bridge_result:
                logger.warning("Instagram DM browser bridge error: %s", bridge_result["error"])
                return 0
            convos_result = {"data": [
                {
                    "id": c.get("id", ""),
                    "participants": {
                        "data": [
                            {"id": c.get("participant_id", ""), "name": c.get("name", "Unknown")}
                        ]
                    },
                }
                for c in bridge_result.get("conversations", [])
            ]}
        else:
            return 0
    except InstagramAPIError as exc:
        logger.warning("Instagram DM API error for account %s: %s", account.id, exc)
        return 0
    except Exception as exc:
        logger.warning("Instagram DM fetch failed for account %s: %s", account.id, exc)
        return 0

    conversations = convos_result.get("data", [])
    if not conversations:
        return 0

    for convo in conversations[:20]:
        convo_id = convo.get("id", "")
        if not convo_id:
            continue

        # Get participant info
        participants = convo.get("participants", {}).get("data", [])
        convo_name = participants[0].get("name", "Unknown") if participants else "Unknown"
        recipient_id = participants[0].get("id", "") if participants else ""

        if not recipient_id:
            continue

        try:
            # 2. Read messages in this conversation
            if client is not None:
                msgs_result = await client.get_dm_messages(convo_id, limit=20)
                messages = msgs_result.get("data", [])
                sender_key = "from"
                sender_field = "id"
                text_field = "message"
            elif bridge is not None:
                bridge_msgs = await bridge.get_instagram_dm_messages(convo_id)
                if "error" in bridge_msgs:
                    continue
                messages = bridge_msgs.get("messages", [])
                sender_key = "is_sent_by_viewer"
                sender_field = None
                text_field = "text"
            else:
                continue

            if not messages:
                continue

            # 3. Find ALL unread inbound messages (not from us)
            my_id = ig_user_id or meta.get("private_api_ds_user_id", "")
            seen_key = str(convo_id)
            last_seen_text = seen.get(seen_key, "")

            # Collect all inbound messages (not sent by viewer)
            inbound_messages = []
            for msg in reversed(messages):
                if sender_field:
                    sender = msg.get(sender_key, {})
                    sender_id = sender.get(sender_field, "") if isinstance(sender, dict) else str(sender)
                    is_outbound = sender_id == my_id
                elif sender_key == "is_sent_by_viewer":
                    is_outbound = bool(msg.get(sender_key, False))
                else:
                    sender_id = msg.get(sender_key, "")
                    is_outbound = sender_id == my_id
                if not is_outbound and msg.get(text_field):
                    msg_text = (msg.get(text_field) or "").strip()
                    if msg_text:
                        inbound_messages.append(msg_text)

            if not inbound_messages:
                continue

            # 4. Check if already replied — compare the latest message
            latest_text = inbound_messages[-1]
            if last_seen_text == latest_text:
                continue

            # 5. Combine all unread inbound messages for context
            # If there are multiple unread messages, combine them so the bot
            # can address ALL the user's queries, not just the last one.
            if len(inbound_messages) > 1:
                text = "\n".join(inbound_messages)
                logger.info(
                    "Instagram DM: %d unread messages from '%s', combining for context",
                    len(inbound_messages), convo_name,
                )
            else:
                text = inbound_messages[0]

            if not text:
                continue

            # 5. Check cooldown
            cooldown_seconds = config.get("cooldown_seconds", 300)
            if not await check_cooldown(account.id, seen_key, cooldown_seconds):
                continue

            # 6. Check human handoff
            if await is_thread_paused(account.id, seen_key):
                continue

            # Lead capture (scaffold): intercept and run a simple qualification flow.
            try:
                from app.models.lead import LeadSource
                from app.services.lead_capture import handle_lead_capture_message

                lead_reply = await handle_lead_capture_message(
                    db,
                    team_id=account.team_id,
                    source=LeadSource.instagram_dm,
                    social_account_id=account.id,
                    thread_id=seen_key,
                    inbound_text=text,
                    postback_payload="",
                    meta_data={
                        "instagram_conversation_id": convo_id,
                        "instagram_recipient_id": recipient_id,
                    },
                )
                if lead_reply:
                    if client is not None:
                        await client.send_dm(recipient_id, lead_reply.text)
                    elif bridge is not None:
                        await bridge.send_instagram_dm_message(recipient_id, lead_reply.text)
                    try:
                        if client is not None:
                            await client.mark_dm_read(convo_id)
                    except Exception:
                        pass

                    await store_message_memory(account.team_id, account.id, seen_key, "them", text)
                    await store_message_memory(account.team_id, account.id, seen_key, "me", lead_reply.text)
                    seen[seen_key] = text
                    await set_cooldown(account.id, seen_key, cooldown_seconds)
                    replies_sent += 1
                    await asyncio.sleep(2)
                    continue
            except Exception as exc:
                logger.debug("Instagram lead capture handler failed (non-fatal): %s", exc)

            # 7. Send typing indicator (feels more natural)
            try:
                if client is not None:
                    await client.send_typing_indicator(recipient_id)
            except Exception:
                pass  # Non-fatal

            # 8. Detect intent and retrieve brand context (RAG)
            intent = await detect_intent(text, cf_token, cf_account, dmr_url)
            brand_context = await retrieve_brand_context(text)

            # 9. Generate AI reply
            reply_text = await generate_contextual_reply(
                config, text, account_name,
                account.id, seen_key,
                cf_token, cf_account, dmr_url,
                intent=intent,
                brand_context=brand_context, team_id=account.team_id,
            )

            if not reply_text:
                reply_text = config.get("fallback_text", "Thanks for your message! I'll get back to you soon.")

            # 9. Send reply via Instagram Messaging API
            send_ok = False
            try:
                if client is not None:
                    await client.send_dm(recipient_id, reply_text)
                    send_ok = True
                elif bridge is not None:
                    send_result = await bridge.send_instagram_dm_message(recipient_id, reply_text)
                    if isinstance(send_result, dict) and send_result.get("error"):
                        logger.warning(
                            "Instagram DM send failed for %s: %s",
                            account.id, send_result["error"],
                        )
                    else:
                        send_ok = True
            except Exception as exc:
                logger.warning("Instagram DM send failed for %s: %s", account.id, exc)

            if not send_ok:
                continue  # Don't mark as seen if send failed

            # 10. Mark conversation as read
            try:
                if client is not None:
                    await client.mark_dm_read(convo_id)
            except Exception:
                pass  # Non-fatal

            # 11. Store in memory
            await store_message_memory(
                account.team_id, account.id, seen_key,
                "them", text,
            )
            await store_message_memory(
                account.team_id, account.id, seen_key,
                "me", reply_text,
            )

            # 12. Mark seen + cooldown
            seen[seen_key] = text
            await set_cooldown(account.id, seen_key, cooldown_seconds)
            replies_sent += 1
            logger.info(
                "Instagram auto-reply sent to '%s' (conversation %s) for account %s",
                convo_name, convo_id, account.id,
            )

            await asyncio.sleep(3)

        except InstagramAPIError as exc:
            logger.warning("Instagram DM API error in conversation %s: %s", convo_id, exc)
            if exc.status_code == 429:
                logger.warning("Instagram rate-limited — stopping for account %s", account.id)
                break
        except Exception as exc:
            logger.error(
                "Error processing Instagram conversation %s: %s",
                convo_id, exc, exc_info=True,
            )

    return replies_sent
