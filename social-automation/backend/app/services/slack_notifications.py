"""Slack helpers for SocialAuto digests and operational alerts.

Design goals:
- Prefer Incoming Webhooks (simple, channel-scoped).
- Fallback to Slack Web API (bot/user token + channel id) when webhook absent.
- Best-effort: missing config should never crash API/workers.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _get_slack_token() -> str:
    settings = get_settings()
    return ((settings.SLACK_BOT_TOKEN or "").strip() or (settings.SLACK_ACCESS_TOKEN or "").strip())


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


async def post_alert_to_slack(text: str, blocks: list[dict] | None = None) -> None:
    """Post an operational alert to #socialauto-alerts (best-effort, non-fatal).

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


# ---------------------------------------------------------------------------
# Block Kit helpers for actionable alerts
#
# action_ids starting with "socialauto_" are handled by the Cloudless web
# app's /api/slack/interactions endpoint, which proxies into this API as the
# admin user. Keep the ids in sync with src/app/api/slack/interactions/route.ts
# in the cloudless.gr repo.
# ---------------------------------------------------------------------------

SOCIALAUTO_ADMIN_URL = "https://social.cloudless.gr"


def _button(text: str, action_id: str, *, value: str = "", style: str | None = None,
            url: str | None = None, confirm: str | None = None) -> dict:
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
                _button("Open SocialAuto", "open_socialauto_admin",
                        url=f"{SOCIALAUTO_ADMIN_URL}/admin"),
            ],
        }
    ]


def publish_failure_buttons(queue_id: str) -> list[dict]:
    """Actions block for publish-failure alerts — retry/cancel the queue item."""
    elements: list[dict] = []
    if queue_id and queue_id != "unknown":
        elements.append(
            _button("Retry", "socialauto_retry_queue", value=queue_id, style="primary")
        )
        elements.append(
            _button("Skip (cancel)", "socialauto_cancel_queue", value=queue_id,
                    style="danger",
                    confirm="Cancel this queue item permanently? The post won't publish to this target.")
        )
    elements.append(
        _button("Open queue", "open_socialauto_queue",
                url=f"{SOCIALAUTO_ADMIN_URL}/admin")
    )
    return [{"type": "actions", "elements": elements}]


async def post_digest_text_to_slack(text: str) -> tuple[bool, str | None, str | None]:
    """Post the full daily digest to #socialauto (returns ok, error, message_ts if token path)."""
    settings = get_settings()
    # Keep a hard fallback so a blank SLACK_CHANNEL_ID doesn't silently break
    # token-based posting in dev.
    channel_id = (settings.SLACK_CHANNEL_ID or "").strip() or "C0C1F1K3DDF"
    return await _post_slack_text(
        text=text,
        webhook_url=settings.SLACK_WEBHOOK_URL,
        token=_get_slack_token(),
        channel_id=channel_id,
        purpose="digest",
    )


async def post_publishing_to_slack(text: str) -> None:
    """Post publish-success notifications to #socialauto-publishing (best-effort)."""
    settings = get_settings()
    webhook = (settings.SLACK_PUBLISHING_WEBHOOK_URL or "").strip()
    if not webhook:
        return
    ok, err, _ = await _post_slack_text(
        text=text,
        webhook_url=webhook,
        token="",
        channel_id="",
        purpose="publishing",
    )
    if not ok:
        logger.warning("Slack publishing webhook failed: %s", err)


async def post_telegram_to_slack(text: str) -> tuple[bool, str | None]:
    """Post Telegram channel events to #socialauto-telegram.

    Uses ``SLACK_TELEGRAM_*`` settings, falling back to the main digest
    webhook/channel when unset (same pattern as lead notifications).
    """
    settings = get_settings()
    webhook_url = (settings.SLACK_TELEGRAM_WEBHOOK_URL or "").strip() or settings.SLACK_WEBHOOK_URL
    channel_id = (settings.SLACK_TELEGRAM_CHANNEL_ID or "").strip() or (
        settings.SLACK_CHANNEL_ID or ""
    ).strip()
    if not webhook_url and not channel_id:
        return False, "Telegram Slack channel not configured"
    ok, err, _ = await _post_slack_text(
        text=text,
        webhook_url=webhook_url,
        token=_get_slack_token(),
        channel_id=channel_id,
        purpose="telegram",
    )
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

