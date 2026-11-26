"""Personal Messenger poller over the Matrix lane (mautrix-meta bridge).

Durable replacement for the browser-sidecar lane: the mautrix-meta bridge
holds the Facebook session at protocol level (E2EE-safe via Armadillo),
mirrors personal Messenger threads into Matrix portal rooms on the
internal continuwuity homeserver, and this task replies over the plain
Matrix client API — no browser, no Playwright, immune to DOM changes.

Flow per run:
    1. Find Facebook personal (user) accounts with auto-reply enabled
       (same ``meta_data.personal_messenger_auto_reply`` config the
       browser lane uses — one config drives both lanes).
    2. Incremental ``GET /sync`` on the bridge-linked Matrix user
       (token from the secret store: ``MATRIX_ADMIN_TOKEN``).
    3. For each portal room whose newest message is from a ghost user
       (``@facebook_*``), run the shared reply pipeline:
       cooldown → pause check → intent detect → brand RAG →
       ``generate_contextual_reply`` (DMR-first, CF fallback).
    4. Send the reply as the bridge-linked user — mautrix-meta forwards
       it to the real Messenger thread.

State:
    - ``matrix:messenger:sync_since`` (Redis): /sync since-token.
      First-ever run only stores the token (bootstrap) so we never
      answer stale history after a deploy.
    - ``meta_data.matrix_messenger_seen``: {room_id: last_replied_event_id}
      (separate from ``personal_messenger_seen`` so the two lanes don't
      clobber each other during migration).
"""

import asyncio
import logging
import re
import uuid as _uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

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
from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()

logger = logging.getLogger(__name__)

_worker_db = task_session

_HS_URL_DEFAULT = "http://continuwuity:6167"
_SYNC_SINCE_KEY = "matrix:messenger:sync_since"
_GHOST_RE = re.compile(r"^@facebook_.*:")
_SELF_MXID = "@socialauto:socialauto.internal"


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
    return run_async(coro)


async def _matrix_token() -> str:
    from app.services.secret_store import secret_store

    return await secret_store.get("MATRIX_ADMIN_TOKEN") or ""


async def _sync_since():
    import redis.asyncio as aioredis

    settings = get_settings()
    url = getattr(settings, "MESSENGER_REDIS_URL", None) or settings.REDIS_URL
    r = aioredis.from_url(url)
    try:
        v = await r.get(_SYNC_SINCE_KEY)
        return v.decode() if v else ""
    finally:
        await r.aclose()


async def _set_sync_since(value: str):
    import redis.asyncio as aioredis

    settings = get_settings()
    url = getattr(settings, "MESSENGER_REDIS_URL", None) or settings.REDIS_URL
    r = aioredis.from_url(url)
    try:
        await r.set(_SYNC_SINCE_KEY, value)
    finally:
        await r.aclose()


@celery_app.task(name="app.worker.tasks.matrix_messenger.poll_matrix_messenger")
def poll_matrix_messenger() -> dict:
    """Poll the Matrix bridge for new personal-Messenger messages and reply."""
    return _run_async(_poll_matrix_messenger_async())


async def _poll_matrix_messenger_async() -> dict:
    stats = {"accounts_checked": 0, "rooms_seen": 0, "replies_sent": 0, "errors": 0}
    settings = get_settings()
    hs_url = getattr(settings, "MATRIX_HS_URL", None) or _HS_URL_DEFAULT
    cf_token = getattr(settings, "CLOUDFLARE_API_TOKEN", "") or ""
    cf_account = getattr(settings, "CLOUDFLARE_ACCOUNT_ID", "") or ""
    dmr_url = getattr(settings, "DMR_BASE_URL", "http://host.docker.internal:12435")

    token = await _matrix_token()
    if not token:
        logger.info("Matrix lane: no MATRIX_ADMIN_TOKEN in secret store, skipping")
        return stats

    since = await _sync_since()

    async with _worker_db() as db:
        result = await db.execute(
            select(SocialAccount).where(
                SocialAccount.platform == "facebook",
                SocialAccount.account_type == "user",
                SocialAccount.status == "active",
            )
        )
        enabled = [a for a in result.scalars().all() if (a.meta_data or {}).get("personal_messenger_auto_reply", {}).get("enabled")]
        if not enabled:
            return stats
        if len(enabled) > 1:
            logger.warning("Matrix lane: %d enabled personal accounts — using %s only", len(enabled), enabled[0].id)
        account = enabled[0]
        meta = account.meta_data or {}
        auto_reply = meta.get("personal_messenger_auto_reply", {})
        seen = meta.get("matrix_messenger_seen", {})
        account_name = getattr(account, "account_name", "") or getattr(account, "name", "") or "user"
        stats["accounts_checked"] = 1

        async with httpx.AsyncClient(base_url=hs_url, timeout=45) as client:
            params = {"timeout": 0, "full_state": "false"}
            if since:
                params["since"] = since
            else:
                params["filter"] = (
                    '{"room":{"timeline":{"limit":1},"state":{"lazy_load_members":true},'
                    '"ephemeral":{"lazy_load_members":true},"account_data":{"lazy_load_members":true}}}'
                )
            try:
                r = await client.get(
                    "/_matrix/client/v3/sync",
                    params=params,
                    headers={"Authorization": f"Bearer {token}"},
                )
                r.raise_for_status()
                sync = r.json()
            except Exception as exc:
                stats["errors"] += 1
                logger.error("Matrix lane: /sync failed: %s", exc)
                return stats

            next_batch = sync.get("next_batch")
            if not since:
                # Bootstrap: never reply to pre-existing history.
                if next_batch:
                    await _set_sync_since(next_batch)
                logger.info("Matrix lane: bootstrap sync complete (since=%s), no replies", next_batch)
                return stats

            # Auto-accept portal invites — mautrix-meta invites the bridge user
            # to new portal rooms; timelines only appear once membership is join.
            invited = (sync.get("rooms") or {}).get("invite") or {}
            for invite_room_id in invited:
                try:
                    await client.post(
                        f"/_matrix/client/v3/rooms/{invite_room_id}/join",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    stats["rooms_joined"] = stats.get("rooms_joined", 0) + 1
                except Exception as exc:
                    logger.warning("Matrix lane: join %s failed: %s", invite_room_id, exc)

            joined = (sync.get("rooms") or {}).get("join") or {}
            cooldown_seconds = auto_reply.get("cooldown_seconds", 300)

            for room_id, room in joined.items():
                events = ((room.get("timeline") or {}).get("events")) or []
                # newest-first scan: find last message event and its sender
                last_msg = None
                for ev in reversed(events):
                    if ev.get("type") == "m.room.message" and (ev.get("content") or {}).get("msgtype") == "m.text":
                        last_msg = ev
                        break
                if not last_msg:
                    continue
                stats["rooms_seen"] += 1
                sender = last_msg.get("sender") or ""
                if not _GHOST_RE.match(sender):
                    continue  # newest message is ours/the bot's — nothing to answer
                event_id = last_msg.get("event_id") or ""
                if seen.get(room_id) == event_id:
                    continue
                text = ((last_msg.get("content") or {}).get("body") or "").strip()
                if not text:
                    continue

                thread_id = room_id  # Matrix room is the conversation handle
                try:
                    if not await check_cooldown(account.id, thread_id, cooldown_seconds):
                        continue
                    if await is_thread_paused(account.id, thread_id):
                        logger.debug("Matrix lane: %s paused, skipping", room_id)
                        continue

                    # Lead capture: trigger/mid-flow DMs get the qualification
                    # flow instead of the bot persona. Recruiting keywords do
                    # not overlap the triggers — recruiters keep the bot path.
                    try:
                        from app.models.lead import LeadSource
                        from app.services.lead_capture import handle_lead_capture_message

                        lead_reply = await handle_lead_capture_message(
                            db,
                            team_id=account.team_id,
                            source=LeadSource.facebook_messenger,
                            social_account_id=account.id,
                            thread_id=thread_id,
                            inbound_text=text,
                            postback_payload="",
                            meta_data={
                                "channel": "personal_messenger",
                                "room_id": room_id,
                                "sender_mxid": sender,
                            },
                        )
                        if lead_reply:
                            txn = f"lead{_uuid.uuid4().hex}"
                            await client.put(
                                f"/_matrix/client/v3/rooms/{room_id}/send/m.room.message/{txn}",
                                json={"msgtype": "m.text", "body": lead_reply.text},
                                headers={"Authorization": f"Bearer {token}"},
                            )
                            await set_cooldown(account.id, thread_id, cooldown_seconds)
                            seen[room_id] = event_id
                            stats["replies_sent"] += 1
                            try:
                                await store_message_memory(account.team_id, account.id, thread_id, sender, text)
                                await store_message_memory(account.team_id, account.id, thread_id, "assistant", lead_reply.text)
                            except Exception:
                                pass
                            logger.info("Matrix lane: lead-capture reply in %s", room_id)
                            continue
                    except Exception as exc:
                        logger.debug("Matrix lane: lead capture failed (non-fatal): %s", exc)

                    intent = await detect_intent(text, cf_token, cf_account, dmr_url)
                    brand_context = await retrieve_brand_context(text)
                    reply_text = await generate_contextual_reply(
                        auto_reply,
                        text,
                        account_name,
                        account.id,
                        thread_id,
                        cf_token,
                        cf_account,
                        dmr_url,
                        intent=intent,
                        brand_context=brand_context,
                        team_id=account.team_id,
                    )
                    if not reply_text:
                        continue

                    # typing indicator → bridge forwards to Messenger
                    try:
                        await client.put(
                            f"/_matrix/client/v3/rooms/{room_id}/typing/{_SELF_MXID}",
                            json={"typing": True, "timeout": 3000},
                            headers={"Authorization": f"Bearer {token}"},
                        )
                    except Exception:
                        pass

                    txn = f"reply{_uuid.uuid4().hex}"
                    await client.put(
                        f"/_matrix/client/v3/rooms/{room_id}/send/m.room.message/{txn}",
                        json={"msgtype": "m.text", "body": reply_text},
                        headers={"Authorization": f"Bearer {token}"},
                    )

                    await set_cooldown(account.id, thread_id, cooldown_seconds)
                    seen[room_id] = event_id
                    stats["replies_sent"] += 1
                    try:
                        await store_message_memory(account.team_id, account.id, thread_id, sender, text)
                        await store_message_memory(account.team_id, account.id, thread_id, "assistant", reply_text)
                    except Exception:
                        pass
                    logger.info("Matrix lane: replied in %s (%d chars)", room_id, len(reply_text))
                except Exception as exc:
                    stats["errors"] += 1
                    logger.error("Matrix lane: room %s failed: %s", room_id, exc, exc_info=True)

            if next_batch:
                await _set_sync_since(next_batch)

            meta["matrix_messenger_seen"] = seen
            meta["matrix_messenger_last_checked"] = datetime.now(UTC).isoformat()
            account.meta_data = meta
            flag_modified(account, "meta_data")
            await db.commit()

    logger.info("Matrix Messenger poll complete: %s", stats)
    return stats
