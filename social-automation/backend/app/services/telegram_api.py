"""Telegram Bot API client (official HTTPS Bot API).

Docs: https://core.telegram.org/bots/api

All queries: ``https://api.telegram.org/bot<token>/<method>``
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import quote

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
        bot_id, sep, secret = token.partition(":")
        if not (
            sep
            and bot_id.isdigit()
            and secret
            and all(c.isascii() and (c.isalnum() or c in "_-") for c in secret)
        ):
            raise ValueError("Invalid Telegram bot token format")
        self.bot_token = token
        self.timeout = timeout
        # quote() is identity for the validated charset; it also makes the
        # host/path boundary explicit to static taint analysis (SSRF guard).
        self._base = f"{TELEGRAM_API_BASE}/bot{quote(token, safe=':')}"

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

    # ── Media sends ─────────────────────────────────────────────────
    # Photo/video/document accepts a public HTTPS URL (Telegram servers fetch
    # it), an existing file_id, or attach://<name> for multipart upload.

    async def send_photo(
        self,
        chat_id: int | str,
        photo: str,
        *,
        caption: str | None = None,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
        disable_notification: bool = False,
    ) -> dict[str, Any]:
        """Official ``sendPhoto`` — photo as URL, file_id, or attach:// name."""
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "photo": photo,
            "disable_notification": disable_notification,
        }
        if caption:
            payload["caption"] = caption[:1024]
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup
        result = await self._call("sendPhoto", payload)
        return result if isinstance(result, dict) else {}

    async def send_video(
        self,
        chat_id: int | str,
        video: str,
        *,
        caption: str | None = None,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
        disable_notification: bool = False,
    ) -> dict[str, Any]:
        """Official ``sendVideo``."""
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "video": video,
            "disable_notification": disable_notification,
        }
        if caption:
            payload["caption"] = caption[:1024]
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup
        result = await self._call("sendVideo", payload)
        return result if isinstance(result, dict) else {}

    async def send_document(
        self,
        chat_id: int | str,
        document: str,
        *,
        caption: str | None = None,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
        disable_notification: bool = False,
    ) -> dict[str, Any]:
        """Official ``sendDocument`` — e.g. the checklist PDF."""
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "document": document,
            "disable_notification": disable_notification,
        }
        if caption:
            payload["caption"] = caption[:1024]
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup
        result = await self._call("sendDocument", payload)
        return result if isinstance(result, dict) else {}

    async def send_media_group(
        self,
        chat_id: int | str,
        media: list[dict[str, Any]],
        *,
        disable_notification: bool = False,
    ) -> list[dict[str, Any]]:
        """Official ``sendMediaGroup`` — 2-10 photo/video items as an album.

        Each item: {"type": "photo"|"video", "media": <url|file_id|attach://>,
        optional "caption" (≤1024, only on items that should carry one)}.
        """
        if not 2 <= len(media) <= 10:
            raise ValueError("media group requires 2-10 items")
        for item in media:
            if item.get("type") not in ("photo", "video"):
                raise ValueError("media group items must be photo or video")
            if not item.get("media"):
                raise ValueError("media group item missing media")
        result = await self._call(
            "sendMediaGroup",
            {
                "chat_id": chat_id,
                "media": media,
                "disable_notification": disable_notification,
            },
        )
        return result if isinstance(result, list) else []

    async def send_poll(
        self,
        chat_id: int | str,
        question: str,
        options: list[str],
        *,
        is_anonymous: bool = True,
        type: str = "regular",
        allows_multiple_answers: bool = False,
        disable_notification: bool = False,
    ) -> dict[str, Any]:
        """Official ``sendPoll`` — native poll (2-10 options)."""
        if not 2 <= len(options) <= 10:
            raise ValueError("poll requires 2-10 options")
        if not (question or "").strip():
            raise ValueError("question is required")
        result = await self._call(
            "sendPoll",
            {
                "chat_id": chat_id,
                "question": question.strip()[:300],
                "options": [{"text": o[:100]} for o in options],
                "is_anonymous": is_anonymous,
                "type": type,
                "allows_multiple_answers": allows_multiple_answers,
                "disable_notification": disable_notification,
            },
        )
        return result if isinstance(result, dict) else {}

    # ── Message/chat management ─────────────────────────────────────

    async def send_message_with_markup(
        self,
        chat_id: int | str,
        text: str,
        reply_markup: dict[str, Any],
        *,
        parse_mode: str | None = None,
        disable_notification: bool = False,
    ) -> dict[str, Any]:
        """Text message carrying an InlineKeyboardMarkup (CTA buttons)."""
        body = (text or "").strip()
        if not body:
            raise ValueError("text is required")
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": body[:MAX_MESSAGE_CHARS],
            "reply_markup": reply_markup,
            "disable_notification": disable_notification,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        result = await self._call("sendMessage", payload)
        return result if isinstance(result, dict) else {}

    async def answer_callback_query(
        self,
        callback_query_id: str,
        *,
        text: str | None = None,
        show_alert: bool = False,
    ) -> bool:
        """Official ``answerCallbackQuery`` — ack a button press."""
        payload: dict[str, Any] = {
            "callback_query_id": callback_query_id,
            "show_alert": show_alert,
        }
        if text:
            payload["text"] = text[:200]
        result = await self._call("answerCallbackQuery", payload)
        return bool(result)

    async def pin_chat_message(
        self,
        chat_id: int | str,
        message_id: int,
        *,
        disable_notification: bool = True,
    ) -> bool:
        result = await self._call(
            "pinChatMessage",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "disable_notification": disable_notification,
            },
        )
        return bool(result)

    async def unpin_chat_message(
        self, chat_id: int | str, message_id: int | None = None
    ) -> bool:
        payload: dict[str, Any] = {"chat_id": chat_id}
        if message_id is not None:
            payload["message_id"] = message_id
        result = await self._call("unpinChatMessage", payload)
        return bool(result)

    async def edit_message_text(
        self,
        chat_id: int | str,
        message_id: int,
        text: str,
        *,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = (text or "").strip()
        if not body:
            raise ValueError("text is required")
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": body[:MAX_MESSAGE_CHARS],
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        result = await self._call("editMessageText", payload)
        return result if isinstance(result, dict) else {}

    async def delete_message(self, chat_id: int | str, message_id: int) -> bool:
        result = await self._call(
            "deleteMessage", {"chat_id": chat_id, "message_id": message_id}
        )
        return bool(result)

    async def get_chat_member_count(self, chat_id: int | str) -> int:
        result = await self._call("getChatMemberCount", {"chat_id": chat_id})
        return int(result) if isinstance(result, int) else 0

    async def export_chat_invite_link(self, chat_id: int | str) -> str:
        """Official ``exportChatInviteLink`` — primary invite link."""
        result = await self._call("exportChatInviteLink", {"chat_id": chat_id})
        return result if isinstance(result, str) else ""

    async def create_chat_invite_link(
        self,
        chat_id: int | str,
        *,
        name: str | None = None,
        expire_date: int | None = None,
        member_limit: int | None = None,
        creates_join_request: bool = False,
    ) -> dict[str, Any]:
        """Official ``createChatInviteLink`` — additional named invite link.

        ``name`` (max 32 chars) labels the link in admin surfaces so join
        sources (e.g. ``ig``/``threads``/``website``) stay attributable.
        Returns the ChatInviteLink object — ``invite_link`` holds the URL.
        """
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "creates_join_request": creates_join_request,
        }
        if name:
            payload["name"] = name[:32]
        if expire_date is not None:
            payload["expire_date"] = expire_date
        if member_limit is not None:
            payload["member_limit"] = member_limit
        result = await self._call("createChatInviteLink", payload)
        return result if isinstance(result, dict) else {}

    async def get_chat_administrators(self, chat_id: int | str) -> list[dict[str, Any]]:
        """Official ``getChatAdministrators`` — list of ChatMember objects."""
        result = await self._call("getChatAdministrators", {"chat_id": chat_id})
        return result if isinstance(result, list) else []

    async def send_chat_action(
        self, chat_id: int | str, action: str = "typing"
    ) -> bool:
        """Official ``sendChatAction`` — typing/upload indicator."""
        if action not in {
            "typing", "upload_photo", "upload_video", "upload_document",
            "choose_sticker", "find_location", "record_voice", "upload_voice",
            "record_video", "upload_video_note", "record_video_note",
        }:
            raise ValueError(f"unknown chat action: {action}")
        result = await self._call(
            "sendChatAction", {"chat_id": chat_id, "action": action}
        )
        return bool(result)


# Update types we subscribe to for group watch + auto-reply.
# Docs: https://core.telegram.org/bots/api#update
TELEGRAM_ALLOWED_UPDATES = [
    "message",
    "edited_message",
    "my_chat_member",
    "chat_member",
    "callback_query",
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


def extract_chat_member_update(update: dict[str, Any]) -> dict[str, Any] | None:
    """Parse ``chat_member`` — a regular user joining/leaving a chat the bot admins.

    For channel joins the update carries ``invite_link`` (the named link used)
    or ``via_join_request`` — the source-attribution signal.
    """
    event = update.get("chat_member")
    if not isinstance(event, dict):
        return None
    chat = event.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return None
    new_member = event.get("new_chat_member") or {}
    old_member = event.get("old_chat_member") or {}
    user = new_member.get("user") or old_member.get("user") or {}
    invite = event.get("invite_link") or {}
    return {
        "update_id": update.get("update_id"),
        "chat_id": chat_id,
        "chat_type": chat.get("type"),
        "chat_title": chat.get("title") or chat.get("username") or "",
        "old_status": old_member.get("status"),
        "new_status": new_member.get("status"),
        "user_id": user.get("id"),
        "username": user.get("username") or "",
        "first_name": user.get("first_name") or "",
        "via_join_request": bool(event.get("via_join_request")),
        "invite_link_name": invite.get("name") or "",
        "invite_link": invite.get("invite_link") or "",
    }


def extract_callback_query(update: dict[str, Any]) -> dict[str, Any] | None:
    """Parse ``callback_query`` (inline-keyboard button press)."""
    cb = update.get("callback_query")
    if not isinstance(cb, dict):
        return None
    from_user = cb.get("from") or {}
    msg = cb.get("message") or {}
    chat = msg.get("chat") or {}
    return {
        "update_id": update.get("update_id"),
        "callback_id": cb.get("id"),
        "data": cb.get("data"),
        "message_id": msg.get("message_id"),
        "chat_id": chat.get("id"),
        "from_user_id": from_user.get("id"),
        "from_username": from_user.get("username"),
    }


def inline_keyboard(rows: list[list[dict[str, str]]]) -> dict[str, Any]:
    """Build an InlineKeyboardMarkup.

    Rows of buttons, each ``{"text": label}`` plus exactly one of
    ``url`` / ``callback_data`` / ``web_app`` — e.g.
    ``inline_keyboard([[{"text": "Get the audit", "url": "https://…"}]])``.
    """
    keyboard = []
    for row in rows:
        out_row = []
        for btn in row:
            b = {"text": str(btn.get("text") or "")[:64]}
            if not b["text"]:
                raise ValueError("button text is required")
            for key in ("url", "callback_data", "web_app"):
                if btn.get(key):
                    b[key] = btn[key] if key != "callback_data" else str(btn[key])[:64]
            if len(b) == 1:
                raise ValueError("button needs url, callback_data, or web_app")
            out_row.append(b)
        keyboard.append(out_row)
    return {"inline_keyboard": keyboard}
