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
            for attempt in range(1, 5):
                try:
                    if webhook_url:
                        resp = await client.post(webhook_url, json={"text": text})
                        if resp.status_code >= 300:
                            last_err = f"Webhook HTTP {resp.status_code}: {resp.text[:200]}"
                            # Webhooks rarely need retry on 4xx.
                            if resp.status_code < 500:
                                return False, last_err, None
                        else:
                            return True, None, None
                    else:
                        # Prefer api.slack.com — bare slack.com TLS often hangs in Docker/WSL.
                        resp = await client.post(
                            "https://api.slack.com/api/chat.postMessage",
                            headers={"Authorization": f"Bearer {token}"},
                            json={"channel": channel_id, "text": text, "mrkdwn": True},
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
                except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as exc:
                    last_err = f"{type(exc).__name__}: {exc or repr(exc)}"
                    logger.warning("Slack %s post attempt %s failed: %s", purpose, attempt, last_err)
                    if attempt < 4:
                        await asyncio.sleep(1.5 * attempt)
                        continue
                    return False, last_err, None
    except Exception as exc:  # noqa: BLE001
        logger.exception("Slack %s post failed", purpose)
        return False, str(exc) or repr(exc), None

    return False, last_err or "unknown error", None


async def post_alert_to_slack(text: str) -> None:
    """Post an operational alert to #socialauto-alerts (best-effort, non-fatal)."""
    settings = get_settings()
    ok, err, _ = await _post_slack_text(
        text=text,
        webhook_url=settings.SLACK_ALERTS_WEBHOOK_URL,
        token=_get_slack_token(),
        channel_id=settings.SLACK_ALERTS_CHANNEL_ID,
        purpose="alerts",
    )
    if not ok:
        logger.warning("Slack alerts disabled or failed: %s", err)


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

