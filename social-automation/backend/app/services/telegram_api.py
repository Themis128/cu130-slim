"""Telegram Bot API client (official HTTPS Bot API).

Docs: https://core.telegram.org/bots/api

All queries: ``https://api.telegram.org/bot<token>/<method>``
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"
MAX_MESSAGE_CHARS = 4096
_SECRET_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,256}$")


def _sanitize_log_text(text: str, max_len: int = 400) -> str:
    cleaned = text.replace("\n", "\\n").replace("\r", "\\r")
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", cleaned)
    return cleaned[:max_len]


class TelegramAPIError(Exception):
    """Raised when Telegram Bot API returns ok=false or HTTP error."""

    def __init__(
        self,
        status_code: int,
        description: str,
        *,
        error_code: int | None = None,
        method: str = "",
    ):
        self.status_code = status_code
        self.description = description
        self.error_code = error_code
        self.method = method
        super().__init__(f"Telegram API {method or 'error'} {status_code}: {description}")

    @property
    def detail(self) -> str:
        return self.description or str(self)


class TelegramAPIClient:
    """Async client for a single bot token."""

    def __init__(self, bot_token: str, *, timeout: float = 30.0):
        token = (bot_token or "").strip()
        if not token or ":" not in token:
            raise ValueError("Invalid Telegram bot token format")
        self.bot_token = token
        self.timeout = timeout
        self._base = f"{TELEGRAM_API_BASE}/bot{token}"

    async def _call(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", method):
            raise TelegramAPIError(400, "Invalid API method name", method="<invalid>")
        url = f"{self._base}/{method}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload or {})
        try:
            data = resp.json()
        except Exception as exc:
            raise TelegramAPIError(
                resp.status_code,
                _sanitize_log_text(resp.text or str(exc)),
                method=method,
            ) from exc
        if not isinstance(data, dict):
            raise TelegramAPIError(resp.status_code, "Invalid JSON response", method=method)
        if not data.get("ok"):
            raise TelegramAPIError(
                resp.status_code if resp.status_code >= 400 else 400,
                str(data.get("description") or "Telegram API error"),
                error_code=data.get("error_code") if isinstance(data.get("error_code"), int) else None,
                method=method,
            )
        return data.get("result")

    async def get_me(self) -> dict[str, Any]:
        """Validate token — returns User object for the bot."""
        result = await self._call("getMe")
        return result if isinstance(result, dict) else {}

    async def set_webhook(
        self,
        url: str,
        *,
        secret_token: str | None = None,
        allowed_updates: list[str] | None = None,
        drop_pending_updates: bool = False,
        max_connections: int = 40,
    ) -> bool:
        """Register HTTPS webhook (mutually exclusive with getUpdates)."""
        if not url.startswith("https://"):
            raise ValueError("Telegram webhooks require an HTTPS URL")
        payload: dict[str, Any] = {
            "url": url,
            "max_connections": max_connections,
            "drop_pending_updates": drop_pending_updates,
        }
        if secret_token is not None:
            if not _SECRET_TOKEN_RE.match(secret_token):
                raise ValueError(
                    "secret_token must be 1-256 chars of A-Z, a-z, 0-9, _ or -"
                )
            payload["secret_token"] = secret_token
        if allowed_updates is not None:
            payload["allowed_updates"] = allowed_updates
        result = await self._call("setWebhook", payload)
        return bool(result)

    async def delete_webhook(self, *, drop_pending_updates: bool = False) -> bool:
        result = await self._call(
            "deleteWebhook",
            {"drop_pending_updates": drop_pending_updates},
        )
        return bool(result)

    async def get_webhook_info(self) -> dict[str, Any]:
        result = await self._call("getWebhookInfo")
        return result if isinstance(result, dict) else {}

    async def send_message(
        self,
        chat_id: int | str,
        text: str,
        *,
        parse_mode: str | None = None,
        disable_notification: bool = False,
        reply_to_message_id: int | None = None,
    ) -> dict[str, Any]:
        """Send a text message (1–4096 characters after entities parsing)."""
        body = (text or "").strip()
        if not body:
            raise ValueError("text is required")
        if len(body) > MAX_MESSAGE_CHARS:
            body = body[:MAX_MESSAGE_CHARS]
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": body,
            "disable_notification": disable_notification,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_to_message_id is not None:
            payload["reply_parameters"] = {"message_id": reply_to_message_id}
        result = await self._call("sendMessage", payload)
        return result if isinstance(result, dict) else {}

    async def forward_message(
        self,
        chat_id: int | str,
        from_chat_id: int | str,
        message_id: int,
        *,
        disable_notification: bool = False,
    ) -> dict[str, Any]:
        """Forward a message (official ``forwardMessage``)."""
        result = await self._call(
            "forwardMessage",
            {
                "chat_id": chat_id,
                "from_chat_id": from_chat_id,
                "message_id": message_id,
                "disable_notification": disable_notification,
            },
        )
        return result if isinstance(result, dict) else {}

    async def copy_message(
        self,
        chat_id: int | str,
        from_chat_id: int | str,
        message_id: int,
        *,
        disable_notification: bool = False,
    ) -> dict[str, Any]:
        """Copy a message without forward header (official ``copyMessage``)."""
        result = await self._call(
            "copyMessage",
            {
                "chat_id": chat_id,
                "from_chat_id": from_chat_id,
                "message_id": message_id,
                "disable_notification": disable_notification,
            },
        )
        return result if isinstance(result, dict) else {}

    async def get_chat(self, chat_id: int | str) -> dict[str, Any]:
        """Official ``getChat`` — ChatFullInfo."""
        result = await self._call("getChat", {"chat_id": chat_id})
        return result if isinstance(result, dict) else {}

    async def get_chat_member(self, chat_id: int | str, user_id: int) -> dict[str, Any]:
        """Official ``getChatMember``."""
        result = await self._call(
            "getChatMember",
            {"chat_id": chat_id, "user_id": user_id},
        )
        return result if isinstance(result, dict) else {}

    async def set_my_commands(
        self,
        commands: list[dict[str, str]],
        *,
        scope: dict[str, Any] | None = None,
    ) -> bool:
        """Official ``setMyCommands`` — shows /commands in Telegram clients."""
        payload: dict[str, Any] = {"commands": commands}
        if scope is not None:
            payload["scope"] = scope
        result = await self._call("setMyCommands", payload)
        return bool(result)

    async def set_chat_description(self, chat_id: int | str, description: str) -> bool:
        result = await self._call(
            "setChatDescription",
            {"chat_id": chat_id, "description": description[:255]},
        )
        return bool(result)


# Update types we subscribe to for group watch + auto-reply.
# Docs: https://core.telegram.org/bots/api#update
TELEGRAM_ALLOWED_UPDATES = [
    "message",
    "edited_message",
    "my_chat_member",
]


def extract_inbound_text_update(update: dict[str, Any]) -> dict[str, Any] | None:
    """Pull chat_id / text / sender from a Bot API Update for auto-reply.

    Handles ``message`` and ``edited_message`` with text or caption.
    Returns None for non-text updates (stickers, etc.).
    """
    msg = update.get("message") or update.get("edited_message")
    if not isinstance(msg, dict):
        return None
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return None
    text = msg.get("text") or msg.get("caption") or ""
    if not str(text).strip():
        return None
    from_user = msg.get("from") or {}
    entities = msg.get("entities") or msg.get("caption_entities") or []
    return {
        "update_id": update.get("update_id"),
        "message_id": msg.get("message_id"),
        "chat_id": chat_id,
        "chat_type": chat.get("type"),
        "chat_title": chat.get("title") or chat.get("username") or "",
        "text": str(text).strip(),
        "entities": entities if isinstance(entities, list) else [],
        "sender_id": from_user.get("id"),
        "sender_username": from_user.get("username"),
        "sender_name": " ".join(
            p for p in (from_user.get("first_name"), from_user.get("last_name")) if p
        ).strip()
        or from_user.get("username")
        or "",
        "is_bot": bool(from_user.get("is_bot")),
    }


def extract_my_chat_member_update(update: dict[str, Any]) -> dict[str, Any] | None:
    """Parse ``my_chat_member`` (bot added/removed/promoted in a chat)."""
    event = update.get("my_chat_member")
    if not isinstance(event, dict):
        return None
    chat = event.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return None
    new_member = event.get("new_chat_member") or {}
    old_member = event.get("old_chat_member") or {}
    return {
        "update_id": update.get("update_id"),
        "chat_id": chat_id,
        "chat_type": chat.get("type"),
        "chat_title": chat.get("title") or chat.get("username") or "",
        "old_status": old_member.get("status"),
        "new_status": new_member.get("status"),
        "from_user_id": (event.get("from") or {}).get("id"),
    }
