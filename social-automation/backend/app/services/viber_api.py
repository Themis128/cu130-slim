"""Viber REST API client (official Bot API).

Docs: https://developers.viber.com/docs/api/rest-bot-api/

All queries: ``POST https://chatapi.viber.com/pa/<method>`` with the bot auth
token in the ``X-Viber-Auth-Token`` header. Viber answers HTTP 200 even on
application errors — the JSON ``status`` field carries the real result
(0 = ok). Webhook authenticity is verified via the
``X-Viber-Content-Signature`` header: hex HMAC-SHA256(auth_token, raw_body).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

VIBER_API_BASE = "https://chatapi.viber.com/pa"
MAX_MESSAGE_CHARS = 7000
MAX_SENDER_NAME_CHARS = 28
MAX_BROADCAST_RECEIVERS = 300

# Application-level status codes returned in the JSON body (HTTP stays 200).
# Docs: https://developers.viber.com/docs/api/rest-bot-api/#error-codes
VIBER_STATUS_CODES = {
    0: "ok",
    1: "invalidUrl",
    2: "invalidAuthToken",
    3: "badData",
    4: "missingData",
    5: "receiverNotRegistered",
    6: "receiverNotSubscribed",
    7: "publicAccountBlocked",
    8: "publicAccountNotFound",
    9: "publicAccountSuspended",
    10: "webhookNotSet",
    11: "receiverNoSuitableDevice",
    12: "tooManyRequests",
    13: "apiVersionNotSupported",
    14: "incompatibleWithVersion",
    15: "publicAccountNotAuthorized",
    16: "inboxMessageStateMismatch",
    17: "unsupportedMessageType",
    18: "messageRateExceeded",
    19: "editMessageFailed",
    20: "unsupportedMedia",
}

# Events the API can push to the webhook once registered.
VIBER_WEBHOOK_EVENTS = [
    "delivered",
    "seen",
    "failed",
    "subscribed",
    "unsubscribed",
    "conversation_started",
    "message",
]


class ViberAPIError(Exception):
    """Raised when the Viber REST API returns a non-zero status or HTTP error."""

    def __init__(
        self,
        status_code: int,
        description: str,
        *,
        viber_status: int | None = None,
        method: str = "",
    ):
        self.status_code = status_code
        self.description = description
        self.viber_status = viber_status
        self.method = method
        super().__init__(
            f"Viber API {method or 'error'} {status_code}: {description}"
            + (
                f" (viber status {viber_status}={VIBER_STATUS_CODES.get(viber_status, '?')})"
                if viber_status is not None
                else ""
            )
        )

    @property
    def detail(self) -> str:
        return self.description or str(self)


def _sanitize_log_text(text: str, max_len: int = 400) -> str:
    cleaned = text.replace("\n", "\\n").replace("\r", "\\r")
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", cleaned)
    return cleaned[:max_len]


class ViberAPIClient:
    """Async client for a single Viber bot auth token."""

    def __init__(self, auth_token: str, *, timeout: float = 30.0):
        token = (auth_token or "").strip()
        if not token or not all(c.isascii() and (c.isalnum() or c in "-_") for c in token):
            raise ValueError("Invalid Viber auth token format")
        self.auth_token = token
        self.timeout = timeout

    async def _call(self, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not re.fullmatch(r"[a-z][a-z_]*", method):
            raise ViberAPIError(400, "Invalid API method name", method="<invalid>")
        url = f"{VIBER_API_BASE}/{method}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                url,
                json=payload or {},
                headers={"X-Viber-Auth-Token": self.auth_token},
            )
        try:
            data = resp.json()
        except Exception as exc:
            raise ViberAPIError(
                resp.status_code,
                _sanitize_log_text(resp.text or str(exc)),
                method=method,
            ) from exc
        if not isinstance(data, dict):
            raise ViberAPIError(resp.status_code, "Invalid JSON response", method=method)
        status = data.get("status")
        if status not in (None, 0):
            raise ViberAPIError(
                400,
                str(data.get("status_message") or "Viber API error"),
                viber_status=status if isinstance(status, int) else None,
                method=method,
            )
        return data

    # ── Account / webhook ───────────────────────────────────────────

    async def get_account_info(self) -> dict[str, Any]:
        """Validate token — returns bot account info (id, name, uri, webhook,
        subscribers_count, event_types)."""
        return await self._call("get_account_info")

    async def set_webhook(
        self,
        url: str,
        *,
        event_types: list[str] | None = None,
        send_name: bool = True,
        send_photo: bool = True,
    ) -> list[str]:
        """Register HTTPS webhook. Returns the list of registered event types."""
        if not url.startswith("https://"):
            raise ValueError("Viber webhooks require an HTTPS URL")
        payload: dict[str, Any] = {
            "url": url,
            "send_name": send_name,
            "send_photo": send_photo,
        }
        if event_types is not None:
            payload["event_types"] = event_types
        data = await self._call("set_webhook", payload)
        events = data.get("event_types")
        return events if isinstance(events, list) else []

    async def unset_webhook(self) -> None:
        """Remove the webhook (set_webhook with an empty url)."""
        await self._call("set_webhook", {"url": ""})

    # ── Messaging ───────────────────────────────────────────────────

    def _sender(self, sender_name: str, sender_avatar: str | None) -> dict[str, Any]:
        sender: dict[str, Any] = {"name": (sender_name or "Bot")[:MAX_SENDER_NAME_CHARS]}
        if sender_avatar:
            sender["avatar"] = sender_avatar
        return sender

    async def send_message(
        self,
        receiver: str,
        message: dict[str, Any],
        *,
        sender_name: str = "Bot",
        sender_avatar: str | None = None,
        tracking_data: str | None = None,
        min_api_version: int = 7,
    ) -> dict[str, Any]:
        """Send a typed message to a subscribed user id."""
        if not receiver:
            raise ValueError("receiver is required")
        payload: dict[str, Any] = {
            "receiver": receiver,
            "min_api_version": min_api_version,
            "sender": self._sender(sender_name, sender_avatar),
            **message,
        }
        if tracking_data:
            payload["tracking_data"] = tracking_data[:4096]
        return await self._call("send_message", payload)

    async def send_text(
        self,
        receiver: str,
        text: str,
        *,
        sender_name: str = "Bot",
        sender_avatar: str | None = None,
        tracking_data: str | None = None,
    ) -> dict[str, Any]:
        body = (text or "").strip()
        if not body:
            raise ValueError("text is required")
        return await self.send_message(
            receiver,
            {"type": "text", "text": body[:MAX_MESSAGE_CHARS]},
            sender_name=sender_name,
            sender_avatar=sender_avatar,
            tracking_data=tracking_data,
        )

    async def send_picture(
        self,
        receiver: str,
        media_url: str,
        *,
        text: str = "",
        thumbnail: str | None = None,
        sender_name: str = "Bot",
        sender_avatar: str | None = None,
    ) -> dict[str, Any]:
        if not media_url.startswith("https://"):
            raise ValueError("picture media must be a public HTTPS URL")
        message: dict[str, Any] = {"type": "picture", "media": media_url}
        if text:
            message["text"] = text[:MAX_MESSAGE_CHARS]
        if thumbnail and thumbnail.startswith("https://"):
            message["thumbnail"] = thumbnail
        return await self.send_message(
            receiver, message, sender_name=sender_name, sender_avatar=sender_avatar
        )

    async def send_video(
        self,
        receiver: str,
        media_url: str,
        *,
        size: int,
        duration: int | None = None,
        thumbnail: str | None = None,
        sender_name: str = "Bot",
        sender_avatar: str | None = None,
    ) -> dict[str, Any]:
        if not media_url.startswith("https://"):
            raise ValueError("video media must be a public HTTPS URL")
        message: dict[str, Any] = {
            "type": "video",
            "media": media_url,
            "size": size,
        }
        if duration is not None:
            message["duration"] = duration
        if thumbnail and thumbnail.startswith("https://"):
            message["thumbnail"] = thumbnail
        return await self.send_message(
            receiver, message, sender_name=sender_name, sender_avatar=sender_avatar
        )

    async def send_file(
        self,
        receiver: str,
        media_url: str,
        *,
        size: int,
        file_name: str,
        sender_name: str = "Bot",
        sender_avatar: str | None = None,
    ) -> dict[str, Any]:
        if not media_url.startswith("https://"):
            raise ValueError("file media must be a public HTTPS URL")
        return await self.send_message(
            receiver,
            {"type": "file", "media": media_url, "size": size, "file_name": file_name},
            sender_name=sender_name,
            sender_avatar=sender_avatar,
        )

    async def send_url(
        self,
        receiver: str,
        url: str,
        *,
        sender_name: str = "Bot",
        sender_avatar: str | None = None,
    ) -> dict[str, Any]:
        return await self.send_message(
            receiver,
            {"type": "url", "media": url},
            sender_name=sender_name,
            sender_avatar=sender_avatar,
        )

    async def broadcast_message(
        self,
        broadcast_list: list[str],
        message: dict[str, Any],
        *,
        sender_name: str = "Bot",
        sender_avatar: str | None = None,
        min_api_version: int = 7,
    ) -> dict[str, Any]:
        """Broadcast to subscribed user ids (max 300). Requires Viber approval —
        returns ``publicAccountNotAuthorized`` (status 15) until granted."""
        if not broadcast_list:
            raise ValueError("broadcast_list is required")
        if len(broadcast_list) > MAX_BROADCAST_RECEIVERS:
            raise ValueError(
                f"broadcast_list max is {MAX_BROADCAST_RECEIVERS} receivers"
            )
        payload: dict[str, Any] = {
            "broadcast_list": broadcast_list,
            "min_api_version": min_api_version,
            "sender": self._sender(sender_name, sender_avatar),
            **message,
        }
        return await self._call("broadcast_message", payload)

    async def broadcast_text(
        self,
        broadcast_list: list[str],
        text: str,
        *,
        sender_name: str = "Bot",
        sender_avatar: str | None = None,
    ) -> dict[str, Any]:
        body = (text or "").strip()
        if not body:
            raise ValueError("text is required")
        return await self.broadcast_message(
            broadcast_list,
            {"type": "text", "text": body[:MAX_MESSAGE_CHARS]},
            sender_name=sender_name,
            sender_avatar=sender_avatar,
        )

    # ── Users ───────────────────────────────────────────────────────

    async def get_online(self, user_ids: list[str]) -> dict[str, Any]:
        """Online status for up to 100 subscribed user ids."""
        if not user_ids or len(user_ids) > 100:
            raise ValueError("user_ids must contain 1-100 ids")
        data = await self._call("get_online", {"ids": user_ids})
        return data

    async def get_user_details(self, user_id: str) -> dict[str, Any]:
        if not user_id:
            raise ValueError("user_id is required")
        return await self._call("get_user_details", {"id": user_id})


# ── Webhook verification + parsing ───────────────────────────────────


def verify_signature(raw_body: bytes, signature: str, auth_token: str) -> bool:
    """Verify ``X-Viber-Content-Signature``: hex HMAC-SHA256(token, raw body)."""
    if not signature or not auth_token:
        return False
    expected = hmac.new(auth_token.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def parse_webhook_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Viber webhook callback into a flat dict.

    Events: ``webhook`` (registration ping), ``conversation_started``,
    ``subscribed``, ``unsubscribed``, ``message``, ``delivered``, ``seen``,
    ``failed``.
    """
    event = str(payload.get("event") or "")
    result: dict[str, Any] = {"event": event, "raw": payload}

    if event == "conversation_started":
        user = payload.get("user") or {}
        result.update(
            {
                "user_id": user.get("id"),
                "user_name": user.get("name"),
                "user_country": user.get("country"),
                "user_language": user.get("language"),
                "subscribed": payload.get("subscribed"),
                "context": payload.get("context"),
                "message_token": payload.get("message_token"),
            }
        )
    elif event in ("subscribed", "unsubscribed"):
        user = payload.get("user") or {}
        result.update({"user_id": user.get("id"), "user_name": user.get("name")})
    elif event == "message":
        sender = payload.get("sender") or {}
        message = payload.get("message") or {}
        result.update(
            {
                "user_id": sender.get("id"),
                "user_name": sender.get("name"),
                "user_country": sender.get("country"),
                "user_language": sender.get("language"),
                "message_type": message.get("type"),
                "text": message.get("text") or "",
                "media": message.get("media"),
                "tracking_data": message.get("tracking_data"),
                "message_token": payload.get("message_token"),
            }
        )
    elif event in ("delivered", "seen", "failed"):
        result.update(
            {
                "user_id": payload.get("user_id"),
                "message_token": payload.get("message_token"),
            }
        )
    return result
