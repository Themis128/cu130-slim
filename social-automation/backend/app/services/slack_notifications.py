"""Slack helpers for SocialAuto digests and operational alerts.

Design goals:
- Prefer Incoming Webhooks (simple, channel-scoped).
- Fallback to Slack Web API (bot/user token + channel id) when webhook absent.
- Best-effort: missing config should never crash API/workers.
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _get_slack_token() -> str:
    settings = get_settings()
    return (settings.SLACK_BOT_TOKEN or "").strip() or (settings.SLACK_ACCESS_TOKEN or "").strip()


async def _post_slack_text(
    *,
    text: str,
    webhook_url: str,
    token: str,
    channel_id: str,
    purpose: str,
    blocks: list[dict] | None = None,
) -> tuple[bool, str | None, str | None]:
    webhook_url = (webhook_url or "").strip()
    token = (token or "").strip()
    channel_id = (channel_id or "").strip()

    if not webhook_url and not token:
        return False, f"Slack {purpose} isn’t configured (missing webhook URL or token)", None

    if not webhook_url and not channel_id:
        return False, f"Slack {purpose} isn’t configured (missing channel id for token-based posting)", None

    last_err: str | None = None
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:

            async def _via_token() -> tuple[bool, str | None, str | None]:
                # Prefer api.slack.com — bare slack.com TLS often hangs in Docker/WSL.
                resp = await client.post(
                    "https://api.slack.com/api/chat.postMessage",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "channel": channel_id,
                        "text": text,
                        "mrkdwn": True,
                        **({"blocks": blocks} if blocks else {}),
                    },
                )
                data = resp.json()
                if not data.get("ok"):
                    err = data.get("error") or "unknown"
                    needed = data.get("needed")
                    detail = f"Slack API error: {err}"
                    if needed:
                        detail += f" (needed: {needed})"
                    return False, detail, None
                return True, None, str(data.get("ts") or "") or None

            for attempt in range(1, 5):
                try:
                    if webhook_url:
                        payload: dict = {"text": text}
                        if blocks:
                            payload["blocks"] = blocks
                        if channel_id.startswith("#"):
                            payload["channel"] = channel_id
                        resp = await client.post(webhook_url, json=payload)
                        if resp.status_code >= 300:
                            last_err = f"Webhook HTTP {resp.status_code}: {resp.text[:200]}"
                            # Webhooks rarely need retry on 4xx. A dead or
                            # revoked webhook (404 no_service, 410 gone) is
                            # permanent — fall back to the bot token when
                            # configured instead of dropping the alert
                            # (observed 2026-10-08: app uninstall killed all
                            # configured webhooks at once).
                            if resp.status_code < 500:
                                if token and channel_id:
                                    logger.warning(
                                        "Slack %s webhook dead (%s) — falling back to bot token",
                                        purpose,
                                        resp.status_code,
                                    )
                                    return await _via_token()
                                return False, last_err, None
                        else:
                            return True, None, None
                    else:
                        return await _via_token()
                except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as exc:
                    last_err = f"{type(exc).__name__}: {exc or repr(exc)}"
                    logger.warning("Slack %s post attempt %s failed: %s", purpose, attempt, last_err)
                    if attempt < 4:
                        await asyncio.sleep(1.5 * attempt)
                        continue
                    if webhook_url and token and channel_id:
                        logger.warning("Slack %s webhook unreachable — falling back to bot token", purpose)
                        return await _via_token()
                    return False, last_err, None
    except Exception as exc:  # noqa: BLE001
        logger.exception("Slack %s post failed", purpose)
        return False, str(exc) or repr(exc), None

    return False, last_err or "unknown error", None


async def _fanout_talk(text: str, purpose: str, blocks: list[dict] | None = None) -> None:
    """Mirror the notification into Nextcloud Talk when enabled —
    the Slack migration transport (ops room for everything, approvals
    room for alerts). Best-effort, never raises."""
    try:
        from app.services import nextcloud_talk

        await nextcloud_talk.post_to_talk(text, purpose=purpose, extra=nextcloud_talk.blocks_to_text(blocks))
    except Exception:  # noqa: BLE001
        logger.debug("Talk fanout failed (non-fatal)", exc_info=True)


async def post_alert_to_slack(text: str, blocks: list[dict] | None = None) -> bool:
    """Post an operational alert to #socialauto-alerts (best-effort, non-fatal).

    Returns whether the Slack post succeeded so callers that persist alert
    dedupe state do not treat a failed notification as delivered.

    Pass ``blocks`` for a Block Kit layout — interactive elements (buttons)
    dispatch to the Slack app that owns the webhook (the Cloudless app), whose
    Interactivity URL proxies back into SocialAuto via ``socialauto_*``
    action ids.
    """
    settings = get_settings()
    ok, err, _ = await _post_slack_text(
        text=text,
        webhook_url=settings.SLACK_ALERTS_WEBHOOK_URL,
        token=_get_slack_token(),
        channel_id=settings.SLACK_ALERTS_CHANNEL_ID,
        purpose="alerts",
        blocks=blocks,
    )
    if not ok:
        logger.warning("Slack alerts disabled or failed: %s", err)
    await _fanout_talk(text, "alerts", blocks)
    await _post_ntfy_alert("ops", text)
    return ok


# ---------------------------------------------------------------------------
# Block Kit helpers for actionable alerts
#
# action_ids starting with "socialauto_" are handled by the Cloudless web
# app's /api/slack/interactions endpoint, which proxies into this API as the
# admin user. Keep the ids in sync with src/app/api/slack/interactions/route.ts
# in the cloudless.gr repo.
# ---------------------------------------------------------------------------

SOCIALAUTO_ADMIN_URL = "https://social.cloudless.gr"


def _button(text: str, action_id: str, *, value: str = "", style: str | None = None, url: str | None = None, confirm: str | None = None) -> dict:
    btn: dict = {
        "type": "button",
        "text": {"type": "plain_text", "text": text, "emoji": True},
        "action_id": action_id,
    }
    if url:
        btn["url"] = url
    else:
        btn["value"] = value or action_id
    if style:
        btn["style"] = style
    if confirm:
        btn["confirm"] = {
            "title": {"type": "plain_text", "text": "Are you sure?"},
            "text": {"type": "plain_text", "text": confirm},
            "confirm": {"type": "plain_text", "text": "Confirm"},
            "deny": {"type": "plain_text", "text": "Cancel"},
        }
    return btn


def session_heal_buttons() -> list[dict]:
    """Actions block for dead/expired session alerts — one-click heal + admin."""
    return [
        {
            "type": "actions",
            "elements": [
                _button("Run session heal", "socialauto_session_heal", style="primary"),
                _button("Ops console", "socialauto_ops_status"),
                _button("Open SocialAuto", "open_socialauto_admin", url=f"{SOCIALAUTO_ADMIN_URL}/admin"),
            ],
        }
    ]


def publish_failure_buttons(queue_id: str) -> list[dict]:
    """Actions block for publish-failure alerts — retry/cancel the queue item."""
    elements: list[dict] = []
    if queue_id and queue_id != "unknown":
        elements.append(_button("Retry", "socialauto_retry_queue", value=queue_id, style="primary"))
        elements.append(
            _button(
                "Skip (cancel)",
                "socialauto_cancel_queue",
                value=queue_id,
                style="danger",
                confirm="Cancel this queue item permanently? The post won't publish to this target.",
            )
        )
    elements.append(_button("Open queue", "open_socialauto_queue", url=f"{SOCIALAUTO_ADMIN_URL}/admin"))
    return [{"type": "actions", "elements": elements}]


async def post_digest_text_to_slack(text: str) -> tuple[bool, str | None, str | None]:
    """Post the full daily digest to #socialauto (returns ok, error, message_ts if token path)."""
    settings = get_settings()
    # Keep a hard fallback so a blank SLACK_CHANNEL_ID doesn't silently break
    # token-based posting in dev.
    channel_id = (settings.SLACK_CHANNEL_ID or "").strip() or "C0C1F1K3DDF"
    result = await _post_slack_text(
        text=text,
        webhook_url=settings.SLACK_WEBHOOK_URL,
        token=_get_slack_token(),
        channel_id=channel_id,
        purpose="digest",
    )
    await _fanout_talk(text, "digest")
    await _post_ntfy_alert("digest", text)
    return result


async def post_publishing_to_slack(text: str) -> None:
    """Post publish-success notifications to #socialauto-publishing (best-effort).

    Webhook first; when it is dead (app uninstall → 404) or unset, falls back to
    SLACK_BOT_TOKEN + SLACK_PUBLISHING_CHANNEL_ID (default: SLACK_CHANNEL_ID) —
    the same rotation path the daily digest uses.
    """
    settings = get_settings()
    webhook = (settings.SLACK_PUBLISHING_WEBHOOK_URL or "").strip()
    channel_id = (settings.SLACK_PUBLISHING_CHANNEL_ID or "").strip() or (settings.SLACK_CHANNEL_ID or "").strip()
    if not webhook and not channel_id:
        return
    ok, err, _ = await _post_slack_text(
        text=text,
        webhook_url=webhook,
        token=_get_slack_token(),
        channel_id=channel_id,
        purpose="publishing",
    )
    if not ok:
        logger.warning("Slack publishing notification failed: %s", err)
    await _fanout_talk(text, "publishing")


async def post_telegram_to_slack(text: str) -> tuple[bool, str | None]:
    """Post Telegram channel events to #socialauto-telegram.

    Uses ``SLACK_TELEGRAM_*`` settings, falling back to the main digest
    webhook/channel when unset (same pattern as lead notifications).
    """
    settings = get_settings()
    webhook_url = (settings.SLACK_TELEGRAM_WEBHOOK_URL or "").strip() or settings.SLACK_WEBHOOK_URL
    channel_id = (settings.SLACK_TELEGRAM_CHANNEL_ID or "").strip() or (settings.SLACK_CHANNEL_ID or "").strip()
    if not webhook_url and not channel_id:
        return False, "Telegram Slack channel not configured"
    ok, err, _ = await _post_slack_text(
        text=text,
        webhook_url=webhook_url,
        token=_get_slack_token(),
        channel_id=channel_id,
        purpose="telegram",
    )
    await _fanout_talk(text, "telegram")
    return ok, err


async def post_billing_digest_to_slack(text: str) -> tuple[bool, str | None]:
    """Post the usage/revenue digest to the configured billing channel.

    Uses ``SLACK_BILLING_*`` settings, falling back to legacy ``SLACK_PADDLE_*``.
    """
    settings = get_settings()
    webhook_url = settings.SLACK_BILLING_WEBHOOK_URL or settings.SLACK_PADDLE_WEBHOOK_URL
    channel_id = (settings.SLACK_BILLING_CHANNEL_ID or settings.SLACK_PADDLE_CHANNEL_ID or "").strip()
    if not webhook_url and not channel_id:
        return False, "Billing digest Slack channel not configured"
    ok, err, _ = await _post_slack_text(
        text=text,
        webhook_url=webhook_url,
        token=_get_slack_token(),
        channel_id=channel_id,
        purpose="billing-digest",
    )
    if not ok:
        logger.warning("Slack billing digest failed: %s", err)
    await _fanout_talk(text, "billing-digest")
    return ok, err


async def post_paddle_digest_to_slack(text: str) -> tuple[bool, str | None]:
    """Backward-compatible alias for ``post_billing_digest_to_slack``."""
    return await post_billing_digest_to_slack(text)


async def post_support_report_to_slack(text: str) -> tuple[bool, str | None]:
    """Post a user support/troubleshooting report to the configured support channel."""
    settings = get_settings()
    webhook = (settings.SLACK_SUPPORT_WEBHOOK_URL or "").strip()
    channel_id = (settings.SLACK_SUPPORT_CHANNEL_ID or "").strip()
    if not webhook and not channel_id:
        return False, "Support Slack channel not configured"
    ok, err, _ = await _post_slack_text(
        text=text,
        webhook_url=webhook,
        token=_get_slack_token(),
        channel_id=channel_id,
        purpose="support",
    )
    if not ok:
        logger.warning("Slack support report failed: %s", err)
    await _fanout_talk(text, "support")
    return ok, err


async def post_thread_reply(*, channel_id: str, thread_ts: str, text: str) -> None:
    """Reply in-thread using Slack Web API token (best-effort)."""
    token = _get_slack_token()
    if not token:
        return
    channel_id = (channel_id or "").strip()
    thread_ts = (thread_ts or "").strip()
    if not channel_id or not thread_ts:
        return
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {token}"},
                json={"channel": channel_id, "text": text, "mrkdwn": True, "thread_ts": thread_ts},
            )
            data = resp.json()
            if not data.get("ok"):
                logger.warning("Slack thread reply failed: %s", data.get("error") or "unknown")
    except Exception:
        logger.debug("Slack thread reply failed (non-fatal)", exc_info=True)


async def upload_file_to_slack(
    *,
    content: bytes,
    filename: str,
    channel_id: str,
    title: str | None = None,
    initial_comment: str | None = None,
    mime: str = "application/octet-stream",
    thread_ts: str | None = None,
) -> tuple[bool, str | None, str | None]:
    """Upload a file via the external-upload flow
    (``files.getUploadURLExternal`` → raw PUT → ``files.completeUploadExternal``).

    The legacy ``files.upload`` endpoint was sunset by Slack (Nov 2025) —
    this is the only supported path. Requires ``SLACK_BOT_TOKEN`` with the
    ``files:write`` scope; incoming webhooks cannot upload files.

    Returns ``(ok, file_id, error)``. Fails soft — callers should log and
    continue (digests still ship as text).
    """
    token = _get_slack_token()
    channel_id = (channel_id or "").strip()
    if not token or not channel_id:
        return False, None, "files upload needs SLACK_BOT_TOKEN + channel id"
    if not content:
        return False, None, "empty file content"

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            step1 = await client.post(
                "https://slack.com/api/files.getUploadURLExternal",
                headers={"Authorization": f"Bearer {token}"},
                data={"filename": filename, "length": str(len(content))},
            )
            d1 = step1.json()
            if not d1.get("ok"):
                return False, None, f"getUploadURLExternal: {d1.get('error')}"

            step2 = await client.post(
                d1["upload_url"],
                files={"file": (filename, content, mime)},
            )
            if step2.status_code != 200:
                return False, None, f"upload PUT failed: HTTP {step2.status_code}"

            payload: dict = {
                "files": [{"id": d1["file_id"], "title": title or filename}],
                "channel_id": channel_id,
            }
            if initial_comment:
                payload["initial_comment"] = initial_comment
            if thread_ts:
                payload["thread_ts"] = thread_ts
            step3 = await client.post(
                "https://slack.com/api/files.completeUploadExternal",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
            d3 = step3.json()
            if not d3.get("ok"):
                return False, None, f"completeUploadExternal: {d3.get('error')}"
            return True, d1["file_id"], None
    except Exception as exc:
        logger.debug("Slack file upload failed (non-fatal)", exc_info=True)
        return False, None, str(exc)


# Severity-routed Telegram alerts — private admin channels
TG_CHANNELS = {
    "critical": os.environ.get("TG_CH_CRITICAL", "-1004389402878"),
    "ops": os.environ.get("TG_CH_OPS", "-1004429103353"),
    "intel": os.environ.get("TG_CH_INTEL", "-1004362141941"),
    "digest": os.environ.get("TG_CH_DIGEST", "-1004363123500"),
}

# ntfy fan-out — every severity alert also lands on the phone's ntfy app
# (topic cloudless-alerts; severity maps to ntfy priority 5..2). Best-effort.
_NTFY_PRIO = {"critical": "5", "ops": "4", "intel": "3", "digest": "2"}


def _ntfy_priority_for(severity: str, text: str) -> str:
    """Priority by recurrence, not just severity (2026-11-21 tuning).

    The alert text embeds ``recur=N``; a first-seen incident (recur=0 or
    absent) is most often transient, so it soft-beeps at 3. Persistent
    incidents escalate: recur>=1 -> 4, recur>=8 -> 5. This restores the
    intent of the 6h dedup, which the changing recur counter in the text
    was silently defeating.
    """
    import re

    base = _NTFY_PRIO.get(severity, "3")
    if severity not in ("critical", "ops"):
        return base
    m = re.search(r"\brecur=(\d+)", text)
    recur = int(m.group(1)) if m else 0
    if recur == 0:
        return "3"
    if recur >= 8:
        return "5"
    return "4"


async def _post_ntfy_alert(severity: str, text: str) -> None:
    settings = get_settings()
    base = (getattr(settings, "NTFY_ALERT_BASE_URL", "") or "").rstrip("/")
    tok = (getattr(settings, "NTFY_ALERT_TOKEN", "") or "").strip()
    if not (base and tok):
        return
    topic = (getattr(settings, "NTFY_ALERT_TOPIC", "") or "cloudless-alerts").strip()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{base}/{topic}",
                content=text.encode(),
                headers={
                    "Authorization": f"Bearer {tok}",
                    "Title": f"[{severity}] cloudless",
                    "Priority": _ntfy_priority_for(severity, text),
                    "Tags": severity,
                    # Cloudflare 403s bare python UAs — send a browser UA.
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) ntfy-alert/1.0",
                },
            )
    except Exception:
        logger.debug("ntfy alert fan-out failed (non-fatal)", exc_info=True)


async def _tg_client():
    """Bot client — DB token first, env fallback so P1 'db down' alerts still deliver."""
    from sqlalchemy import select

    from app.models.social_account import SocialAccount
    from app.services.telegram_agent import TelegramAPIClient, _bot_token
    from app.worker._async import task_session

    token = ""
    try:
        async with task_session() as db:
            acc = (await db.execute(select(SocialAccount).where(SocialAccount.platform == "telegram"))).scalars().first()
        token = _bot_token(acc) if acc else ""
    except Exception:
        pass  # DB down — env fallback below keeps P1 alerts deliverable
    if not token:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return None
    try:
        return TelegramAPIClient(token)
    except Exception:
        return None


async def _archive_channel_post(channel: str, msg_id: int | None, text: str) -> None:
    """Best-effort send-time archive into telegram_channel_messages.

    The Bot API never emits ``channel_post`` updates for the bot's own
    posts, so the webhook lane can't see bot-authored alerts — every send
    dual-writes here (same row the Telethon archiver + webhook ingest
    upsert). Never raises.
    """
    if not isinstance(msg_id, int):
        return
    if channel in TG_CHANNELS:  # bare key -> canonical archiver slug
        channel = f"cloudless-{channel}"
    try:
        from datetime import UTC, datetime

        from app.models.telegram_message import TelegramChannelMessage
        from app.worker._async import task_session

        async with task_session() as db:
            db.add(
                TelegramChannelMessage(
                    channel=channel[:32],
                    msg_id=msg_id,
                    date=datetime.now(UTC),
                    sender="Cloudless_newbot",
                    text=str(text)[:8000],
                )
            )
            await db.commit()
    except Exception:
        pass


# ── digest batching (alert-architecture phase 3) ──────────────────────────
# Informational severities never post standalone — they queue in redis and
# flush as ONE batched message on the daily digest pass. The interception
# lives here in the funnel so no emitter needs to change.
DIGEST_SEVERITIES = ("intel", "digest")
DIGEST_QUEUE_KEY = "ops:digest-queue"
DIGEST_FLUSH_KEY = "ops:digest-last-flush"


async def queue_digest(severity: str, text: str) -> bool:
    """Batch an informational alert into the daily digest queue."""
    try:
        import json as _json

        import redis.asyncio as aioredis

        r = aioredis.from_url(get_settings().REDIS_URL, decode_responses=True)
        try:
            import time as _time

            await r.rpush(DIGEST_QUEUE_KEY, _json.dumps({"sev": severity, "text": text, "ts": _time.time()}))
        finally:
            await r.aclose()
        return True
    except Exception:
        return False


async def _stamp_digest_flush() -> None:
    try:
        from datetime import UTC, datetime

        import redis.asyncio as aioredis

        r = aioredis.from_url(get_settings().REDIS_URL, decode_responses=True)
        try:
            # UTC date — the flush window is UTC; a local-tz stamp at late-evening
            # UTC (already tomorrow local) would skip the next morning's digest
            await r.set(DIGEST_FLUSH_KEY, datetime.now(UTC).date().isoformat())
        finally:
            await r.aclose()
    except Exception:
        pass


async def flush_digest_queue() -> int:
    """Send the queued informational alerts as ONE batched digest message.
    Returns the number of batched items (0 when the queue was empty)."""
    import json as _json

    raw: list = []
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(get_settings().REDIS_URL, decode_responses=True)
        try:
            raw = await r.lrange(DIGEST_QUEUE_KEY, 0, -1)
            if raw:
                await r.delete(DIGEST_QUEUE_KEY)
        finally:
            await r.aclose()
    except Exception:
        return 0

    if not raw:
        # empty days still close the window — otherwise the flush time drifts
        # to whenever the first late item arrives
        await _stamp_digest_flush()
        return 0

    lines = []
    for item in raw:
        try:
            entry = _json.loads(item)
            lines.append(f"• [{entry.get('sev', 'intel')}] {str(entry.get('text', ''))[:400]}")
        except Exception:
            lines.append(f"• {str(item)[:400]}")
    body = "📋 daily intel digest — %d item(s)\n\n" % len(lines) + "\n\n".join(lines)
    client = await _tg_client()
    delivered = False
    if client is not None:
        try:
            _m = await client.send_message(TG_CHANNELS["digest"], body[:4000])
            await _archive_channel_post("digest", _m.get("message_id") if isinstance(_m, dict) else None, body[:4000])
            delivered = True
        except Exception:
            delivered = False
    if not delivered:
        # delivery failed — put the items back so the next sweep retries
        # instead of silently dropping the day's reports
        try:
            import redis.asyncio as aioredis

            r = aioredis.from_url(get_settings().REDIS_URL, decode_responses=True)
            try:
                await r.rpush(DIGEST_QUEUE_KEY, *raw)
            finally:
                await r.aclose()
        except Exception:
            pass
        return 0
    await _stamp_digest_flush()
    return len(lines)


async def digest_flushed_today() -> bool:
    try:
        from datetime import UTC, datetime

        import redis.asyncio as aioredis

        r = aioredis.from_url(get_settings().REDIS_URL, decode_responses=True)
        try:
            val = await r.get(DIGEST_FLUSH_KEY)
        finally:
            await r.aclose()
        return val == datetime.now(UTC).date().isoformat()
    except Exception:
        return False


async def send_telegram_alert_tracked(severity: str, text: str) -> tuple[bool, str | None, int | None]:
    """send_telegram_alert returning a receipt — (sent, chat_id, message_id) of the
    primary channel message — so callers can edit/delete it in place later.
    Dedup-suppressed or failed sends return (True/False, None, None)."""
    import hashlib

    DEDUP_WINDOW_S = 6 * 3600
    fp = hashlib.sha256(f"{severity}|{text}".encode()).hexdigest()[:16]

    # ntfy fanout first — dedup guards telegram spam, never the information
    # flow into the incident archive
    await _post_ntfy_alert(severity, text)

    # informational severities batch into the daily digest — nothing
    # informational ever posts standalone (phase-3 architecture)
    if severity in DIGEST_SEVERITIES:
        if await queue_digest(severity, text):
            return True, None, None
        # redis unavailable — fall through to direct posting rather than
        # dropping the report entirely

    try:
        import redis.asyncio as aioredis

        from app.core.config import get_settings

        r = aioredis.from_url(get_settings().REDIS_URL, decode_responses=True)
        try:
            if await r.exists(f"alert-dedup:{fp}"):
                return True, None, None  # already reported — silent ack
        finally:
            await r.aclose()
    except Exception:
        pass  # dedup failure must not block the alert

    chan_key = severity if severity in TG_CHANNELS else "ops"
    chat = TG_CHANNELS[chan_key]
    client = await _tg_client()
    if client is None:
        return False, None, None
    try:
        msg = await client.send_message(chat, text)
        mid = msg.get("message_id") if isinstance(msg, dict) else None
        await _archive_channel_post(chan_key, mid, text)
        if severity == "critical":
            try:
                _m = await client.send_message(TG_CHANNELS["ops"], f"[P1] {text}")
                await _archive_channel_post("ops", _m.get("message_id") if isinstance(_m, dict) else None, f"[P1] {text}")
            except Exception:
                pass  # mirror is best-effort — never sacrifice the primary receipt
    except Exception:
        return False, None, None

    # stamp dedup only AFTER successful delivery — stamping before the send
    # would suppress the retry for the whole window when telegram rejects
    try:
        import redis.asyncio as aioredis

        from app.core.config import get_settings

        r = aioredis.from_url(get_settings().REDIS_URL, decode_responses=True)
        try:
            await r.setex(f"alert-dedup:{fp}", DEDUP_WINDOW_S, "1")
        finally:
            await r.aclose()
    except Exception:
        pass
    return True, str(chat), int(mid) if isinstance(mid, int) else None


async def send_telegram_alert(severity: str, text: str) -> bool:
    """Route an alert to the severity's private admin channel.
    'critical' also posts to ops (continue-matching — complete record).

    Dedup: the same alert text (fingerprint = severity + text) sends at most
    once per DEDUP_WINDOW_S — a heal reporting 'token invalid' every 30min
    alerts once, then stays quiet until the state changes."""
    sent, _, _ = await send_telegram_alert_tracked(severity, text)
    return sent


async def post_ops_closure(text: str) -> bool:
    """Post a resolution closure to the ops record channel — bypasses dedup
    (two same-title episodes closing within 6h must both close the record)
    and the ntfy fanout (closures are record-keeping, not push-worthy)."""
    try:
        client = await _tg_client()
        if client is None:
            return False
        _m = await client.send_message(TG_CHANNELS["ops"], text, disable_notification=True)
        await _archive_channel_post("ops", _m.get("message_id") if isinstance(_m, dict) else None, text)
        return True
    except Exception:
        return False


async def edit_telegram_alert(chat_id: str, message_id: int, text: str) -> bool:
    """Edit an alert message in place (resolution one-liner). Never raises."""
    try:
        client = await _tg_client()
        if client is None:
            return False
        await client.edit_message_text(chat_id, message_id, text)
        return True
    except Exception:
        return False


async def delete_telegram_alert(chat_id: str, message_id: int) -> bool:
    """Delete an alert message (post-resolution grace cleanup). Never raises."""
    try:
        client = await _tg_client()
        if client is None:
            return False
        return bool(await client.delete_message(chat_id, message_id))
    except Exception:
        return False
