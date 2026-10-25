"""Telegram gate alerts via the telegram-mcp HTTP server (host :8765).

Sends through the user's own Telethon session — no bot token needed.
Target: Saved Messages ("me") or TELEGRAM_GATE_CHAT_ID.
"""
from __future__ import annotations

import logging
import os

import httpx

from app.core.config import get_settings
from app.services.email_digest import send_email
from app.services.slack_notifications import post_telegram_to_slack

logger = logging.getLogger(__name__)

BASE = os.environ.get("TELEGRAM_MCP_URL", "http://host.docker.internal:8765/mcp")

_EVENT_LABELS = {
    "member_joined": "New member joined",
    "member_left": "Member left",
    "join_request_approved": "New member joined (join request approved)",
    "bot_admin": "Bot added as administrator",
    "bot_member": "Bot added to chat",
    "bot_removed": "Bot removed from chat",
}


def _notify_recipients() -> list[str] | None:
    settings = get_settings()
    raw = (settings.TELEGRAM_NOTIFY_EMAIL or "").strip()
    if not raw:
        return None  # send_email falls back to DIGEST_EMAIL_TO
    return [a.strip() for a in raw.split(",") if a.strip()] or None


async def notify_channel_event(
    *,
    event: str,
    chat_title: str,
    chat_id: int | str,
    actor: str = "",
    details: list[str] | None = None,
) -> dict[str, bool]:
    """Fan a channel event out to #socialauto-telegram and the notify email.

    Returns ``{"slack": ok, "email": ok}`` — never raises.
    """
    label = _EVENT_LABELS.get(event, event.replace("_", " ").title())
    lines = [f"*{label}* — {chat_title or chat_id}"]
    if actor:
        lines.append(f"by {actor}")
    lines.extend(details or [])
    lines.append(f"chat_id `{chat_id}`")
    text = "\n".join(lines)

    slack_ok = False
    try:
        slack_ok, err = await post_telegram_to_slack(text)
        if not slack_ok:
            logger.warning("Telegram notify → Slack failed: %s", err)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Telegram notify → Slack raised: %s", exc)

    email_ok = False
    try:
        await send_email(
            subject=f"[SocialAuto] Telegram: {label} — {chat_title or chat_id}",
            text_body=text.replace("*", ""),
            to_addrs=_notify_recipients(),
        )
        email_ok = True
    except Exception as exc:
        logger.warning("Telegram notify → email failed: %s", exc)

    return {"slack": slack_ok, "email": email_ok}


async def _rpc(client, method, params=None, sid=None):
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    if sid:
        headers["Mcp-Session-Id"] = sid
    r = await client.post(BASE, headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": method,
        "params": params or {}})
    return r


async def send_telegram(text: str, chat: str = "me") -> bool:
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            init = await _rpc(c, "initialize", {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "socialauto", "version": "1"}})
            sid = init.headers.get("mcp-session-id")
            await _rpc(c, "notifications/initialized", sid=sid)
            await _rpc(c, "tools/call", {
                "name": "send_message",
                "arguments": {"chat_id": chat, "message": text}}, sid=sid)
        return True
    except Exception as e:  # noqa: BLE001 — alerting never breaks callers
        logger.warning("telegram send failed: %s", e)
        return False
