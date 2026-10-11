"""Telegram channel event notifications — Slack + email.

Best-effort fan-out for channel events observed via the bot webhook
(membership changes, joins, leaves, invite-link attribution). A failure in
either transport logs a warning and never raises into the webhook path.
"""
from __future__ import annotations

import logging

from app.core.config import get_settings
from app.services.email_digest import send_email
from app.services.slack_notifications import post_telegram_to_slack

logger = logging.getLogger(__name__)

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
