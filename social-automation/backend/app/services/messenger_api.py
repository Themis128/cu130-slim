"""Facebook Messenger Platform API client.

Wraps the Messenger Platform Graph API endpoints for:
- Page subscription to messaging webhooks
- Messenger Profile configuration (greeting, Get Started, persistent menu)
- Send API (text, media, templates)
- Conversations API (list threads, read messages)
- User Profile API (PSID -> name, avatar)
- Thread control / handover

A Messenger "account" in SocialAuto is a Facebook Page that has been
subscribed to the Messenger Platform.  The Page access token (stored in
``social_accounts.meta_data.page_token``) is used for all calls.

API reference:
- https://developers.facebook.com/docs/messenger-platform/reference/messenger-profile-api
- https://developers.facebook.com/docs/messenger-platform/send-messages/
- https://developers.facebook.com/docs/messenger-platform/conversations/
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from app.services.facebook_api import FacebookAPIError, _sanitize_log_text, _validate_id

FACEBOOK_GRAPH_BASE = "https://graph.facebook.com"
DEFAULT_API_VERSION = "v26.0"
DEFAULT_TIMEOUT = 60.0

logger = logging.getLogger(__name__)

# PSIDs are numeric strings assigned per Page-person pair.
_PSID_RE = re.compile(r"^[0-9]{1,32}$")


def _validate_psid(value: str) -> str:
    """Validate a Page-Scoped ID (PSID)."""
    value = value.strip()
    if not value:
        raise ValueError("PSID is empty")
    if not _PSID_RE.match(value):
        raise ValueError(f"Invalid PSID format: {value[:40]}")
    return value


# ------------------------------------------------------------------
# Messenger Profile defaults
# ------------------------------------------------------------------

DEFAULT_GREETING = [
    {"locale": "default", "text": "Hi! 👋 Welcome to {page_name}. How can we help you today?"},
]

DEFAULT_GET_STARTED = {"payload": "GET_STARTED"}

DEFAULT_PERSISTENT_MENU = [
    {
        "locale": "default",
        "composer_input_disabled": False,
        "call_to_actions": [
            {"type": "postback", "title": "🏠 Home", "payload": "MENU_HOME"},
            {"type": "postback", "title": "📞 Contact", "payload": "MENU_CONTACT"},
            {"type": "web_url", "title": "🌐 Website", "url": "{page_url}", "webview_height_ratio": "full"},
        ],
    },
]

DEFAULT_WHITELISTED_DOMAINS: list[str] = []

# Fields to subscribe when enabling a Page for Messenger.
SUBSCRIBED_FIELDS = [
    "messages",
    "messaging_postbacks",
    "messaging_optins",
    "message_echoes",
    "messaging_account_linking",
    "message_deliveries",
    "message_reads",
]


class MessengerAPIClient:
    """Async Messenger Platform API client for a single Facebook Page.

    Requires a Page access token with ``pages_messaging`` and
    ``pages_manage_metadata`` permissions.
    """

    def __init__(
        self,
        access_token: str,
        page_id: str,
        api_version: str = DEFAULT_API_VERSION,
    ):
        if not access_token:
            raise ValueError("Page access token is required")
        self.access_token = access_token
        self.page_id = _validate_id(page_id, "page_id")
        self.api_version = api_version.lstrip("/")
        self._base_url = f"{FACEBOOK_GRAPH_BASE}/{self.api_version}"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _params(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"access_token": self.access_token}
        if extra:
            params.update(extra)
        return params

    def _url(self, path: str) -> str:
        path = path.lstrip("/")
        return f"{self._base_url}/{path}"

    def _raise_for_status(self, resp: httpx.Response, url: str) -> None:
        if resp.status_code < 400:
            return
        text = _sanitize_log_text(resp.text)
        safe_url = _sanitize_log_text(url)
        logger.error("Messenger API error %s for %s: %s", resp.status_code, safe_url, text)
        err = FacebookAPIError.from_response(resp, url)
        if resp.status_code >= 500:
            err.status_code = 503 if resp.status_code in (503, 504) else 502
        raise err

    # ------------------------------------------------------------------
    # Page subscription
    # ------------------------------------------------------------------

    async def subscribe_page(self) -> dict:
        """Subscribe the Page to Messenger webhooks.

        POST /{page-id}/subscribed_apps?subscribed_fields=messages,...
        """
        url = self._url(f"{self.page_id}/subscribed_apps")
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(
                url,
                params=self._params({"subscribed_fields": ",".join(SUBSCRIBED_FIELDS)}),
            )
            self._raise_for_status(resp, url)
            return resp.json()

    async def unsubscribe_page(self) -> dict:
        """Remove the app's Messenger subscription from the Page."""
        url = self._url(f"{self.page_id}/subscribed_apps")
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.delete(url, params=self._params())
            self._raise_for_status(resp, url)
            return resp.json()

    async def get_subscribed_apps(self) -> list[dict]:
        """List apps subscribed to the Page."""
        url = self._url(f"{self.page_id}/subscribed_apps")
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(url, params=self._params())
            self._raise_for_status(resp, url)
            return resp.json().get("data", [])

    # ------------------------------------------------------------------
    # Messenger Profile API
    # ------------------------------------------------------------------

    async def get_messenger_profile(self, fields: list[str] | None = None) -> dict:
        """GET /{page-id}/messenger_profile?fields=greeting,get_started,...

        Returns the current Messenger Profile properties.
        """
        if fields is None:
            fields = [
                "greeting",
                "get_started",
                "persistent_menu",
                "whitelisted_domains",
                "ice_breakers",
                "account_linking_url",
                "commands",
            ]
        url = self._url(f"{self.page_id}/messenger_profile")
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(
                url,
                params=self._params({"fields": ",".join(fields)}),
            )
            self._raise_for_status(resp, url)
            data = resp.json()
            # API returns {"data": [{...}]}
            if "data" in data and data["data"]:
                return data["data"][0]
            return {}

    async def set_messenger_profile(self, profile: dict) -> dict:
        """POST /{page-id}/messenger_profile — set one or more properties.

        Only properties included in ``profile`` are overwritten.
        """
        url = self._url(f"{self.page_id}/messenger_profile")
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(
                url,
                params=self._params(),
                json=profile,
                headers={"Content-Type": "application/json"},
            )
            self._raise_for_status(resp, url)
            return resp.json()

    async def delete_messenger_profile_fields(self, fields: list[str]) -> dict:
        """DELETE /{page-id}/messenger_profile — remove specific properties."""
        url = self._url(f"{self.page_id}/messenger_profile")
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.delete(
                url,
                params=self._params(),
                json={"fields": fields},
                headers={"Content-Type": "application/json"},
            )
            self._raise_for_status(resp, url)
            return resp.json()

    async def setup_default_profile(
        self,
        page_name: str = "",
        page_url: str = "",
        greeting_text: str | None = None,
    ) -> dict:
        """Configure a complete default Messenger Profile in one call.

        Sets greeting, Get Started button, persistent menu, and whitelisted
        domains.  ``{page_name}`` and ``{page_url}`` placeholders in the
        defaults are replaced with the provided values.
        """
        greeting = [
            {
                "locale": "default",
                "text": (greeting_text or DEFAULT_GREETING[0]["text"]).replace("{page_name}", page_name or "us"),
            }
        ]
        menu = _interpolate_menu(DEFAULT_PERSISTENT_MENU, page_name=page_name, page_url=page_url)
        profile = {
            "greeting": greeting,
            "get_started": DEFAULT_GET_STARTED,
            "persistent_menu": menu,
        }
        if page_url:
            from urllib.parse import urlparse
            domain = urlparse(page_url).netloc
            if domain:
                profile["whitelisted_domains"] = [f"https://{domain}"]
        return await self.set_messenger_profile(profile)

    # ------------------------------------------------------------------
    # Send API
    # ------------------------------------------------------------------

    async def send_text(
        self,
        recipient_psid: str,
        text: str,
        messaging_type: str = "RESPONSE",
        metadata: str | None = None,
    ) -> dict:
        """Send a text message to a person via the Send API.

        POST /{page-id}/messages
        """
        psid = _validate_psid(recipient_psid)
        body: dict[str, Any] = {
            "recipient": {"id": psid},
            "messaging_type": messaging_type,
            "message": {"text": text},
        }
        if metadata:
            body["message"]["metadata"] = metadata
        url = self._url(f"{self.page_id}/messages")
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(
                url,
                params=self._params(),
                json=body,
                headers={"Content-Type": "application/json"},
            )
            self._raise_for_status(resp, url)
            return resp.json()

    async def send_attachment(
        self,
        recipient_psid: str,
        attachment: dict,
        messaging_type: str = "RESPONSE",
    ) -> dict:
        """Send an attachment (image, audio, video, file, template).

        ``attachment`` must follow the Messenger attachment format:
        {"type": "image", "payload": {"url": "https://..."}}
        """
        psid = _validate_psid(recipient_psid)
        body = {
            "recipient": {"id": psid},
            "messaging_type": messaging_type,
            "message": {"attachment": attachment},
        }
        url = self._url(f"{self.page_id}/messages")
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(
                url,
                params=self._params(),
                json=body,
                headers={"Content-Type": "application/json"},
            )
            self._raise_for_status(resp, url)
            return resp.json()

    async def send_image_url(self, recipient_psid: str, image_url: str) -> dict:
        """Convenience wrapper to send an image by URL."""
        return await self.send_attachment(
            recipient_psid,
            {"type": "image", "payload": {"url": image_url, "is_reusable": True}},
        )

    async def send_quick_replies(
        self,
        recipient_psid: str,
        text: str,
        quick_replies: list[dict],
    ) -> dict:
        """Send text with quick reply buttons.

        ``quick_replies`` is a list of:
        {"content_type": "text", "title": "Yes", "payload": "ANSWER_YES"}
        """
        psid = _validate_psid(recipient_psid)
        body = {
            "recipient": {"id": psid},
            "messaging_type": "RESPONSE",
            "message": {"text": text, "quick_replies": quick_replies},
        }
        url = self._url(f"{self.page_id}/messages")
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(
                url,
                params=self._params(),
                json=body,
                headers={"Content-Type": "application/json"},
            )
            self._raise_for_status(resp, url)
            return resp.json()

    async def send_sender_action(self, recipient_psid: str, action: str) -> dict:
        """Send a sender action (typing_on, typing_off, mark_seen)."""
        psid = _validate_psid(recipient_psid)
        body = {
            "recipient": {"id": psid},
            "sender_action": action,
        }
        url = self._url(f"{self.page_id}/messages")
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(
                url,
                params=self._params(),
                json=body,
                headers={"Content-Type": "application/json"},
            )
            self._raise_for_status(resp, url)
            return resp.json()

    # ------------------------------------------------------------------
    # Conversations API
    # ------------------------------------------------------------------

    async def get_conversations(
        self,
        platform: str = "messenger",
        limit: int = 25,
    ) -> list[dict]:
        """List conversations for the Page.

        GET /{page-id}/conversations?platform=messenger
        """
        url = self._url(f"{self.page_id}/conversations")
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(
                url,
                params=self._params({
                    "platform": platform,
                    "limit": limit,
                    "fields": "id,snippet,updated_time,participants,message_count,unread_count",
                }),
            )
            self._raise_for_status(resp, url)
            return resp.json().get("data", [])

    async def get_conversation_messages(
        self,
        conversation_id: str,
        limit: int = 20,
    ) -> list[dict]:
        """Get messages in a conversation thread."""
        conv_id = _validate_id(conversation_id, "conversation_id")
        url = self._url(conv_id)
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(
                url,
                params=self._params({
                    "fields": f"messages.limit({limit}){{message,from,created_time,id}}",
                }),
            )
            self._raise_for_status(resp, url)
            data = resp.json()
            return data.get("messages", {}).get("data", [])

    # ------------------------------------------------------------------
    # User Profile API
    # ------------------------------------------------------------------

    async def get_user_profile(
        self,
        psid: str,
        fields: list[str] | None = None,
    ) -> dict:
        """Get the profile of a person who messaged the Page.

        Requires Advanced Access for users who are not admins/testers.
        Default fields: first_name, last_name, profile_pic, locale, timezone, gender.
        """
        psid = _validate_psid(psid)
        if fields is None:
            fields = ["first_name", "last_name", "profile_pic", "locale", "timezone", "gender"]
        url = self._url(psid)
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(
                url,
                params=self._params({"fields": ",".join(fields)}),
            )
            self._raise_for_status(resp, url)
            return resp.json()

    # ------------------------------------------------------------------
    # Thread control / handover
    # ------------------------------------------------------------------

    async def pass_thread_control(self, recipient_psid: str, target_app_id: str) -> dict:
        """Pass thread control to another app."""
        psid = _validate_psid(recipient_psid)
        body = {
            "recipient": {"id": psid},
            "target_app_id": target_app_id,
        }
        url = self._url(f"{self.page_id}/pass_thread_control")
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(
                url,
                params=self._params(),
                json=body,
                headers={"Content-Type": "application/json"},
            )
            self._raise_for_status(resp, url)
            return resp.json()

    async def take_thread_control(self, recipient_psid: str) -> dict:
        """Take thread control from another app."""
        psid = _validate_psid(recipient_psid)
        body = {"recipient": {"id": psid}}
        url = self._url(f"{self.page_id}/take_thread_control")
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(
                url,
                params=self._params(),
                json=body,
                headers={"Content-Type": "application/json"},
            )
            self._raise_for_status(resp, url)
            return resp.json()

    async def get_page_info(self) -> dict:
        """Get the Page name and username for greeting interpolation."""
        url = self._url(self.page_id)
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(
                url,
                params=self._params({"fields": "id,name,username,about,website"}),
            )
            self._raise_for_status(resp, url)
            return resp.json()


def _interpolate_menu(menu: list[dict], **kwargs: str) -> list[dict]:
    """Replace {placeholders} in persistent menu URLs and titles."""
    import json

    raw = json.dumps(menu)
    for key, val in kwargs.items():
        raw = raw.replace(f"{{{key}}}", val or "")
    return json.loads(raw)


# ------------------------------------------------------------------
# Webhook event helpers
# ------------------------------------------------------------------

def parse_webhook_event(body: dict) -> list[dict]:
    """Parse a Messenger webhook payload and return normalized events.

    Each event is a dict with:
    - page_id: the Page that received the message
    - sender_psid: the person who sent the message
    - recipient_psid: the Page ID (for replies)
    - timestamp: event timestamp
    - message_text: text content (if any)
    - message_attachments: attachments (if any)
    - postback_payload: postback payload (if any)
    - message_type: "text", "attachment", "postback", "delivery", "read"
    """
    events: list[dict] = []
    if body.get("object") != "page":
        return events

    for entry in body.get("entry", []):
        page_id = entry.get("id")
        for messaging in entry.get("messaging", []):
            sender_psid = messaging.get("sender", {}).get("id", "")
            recipient_psid = messaging.get("recipient", {}).get("id", "")
            timestamp = messaging.get("timestamp", 0)

            event: dict[str, Any] = {
                "page_id": page_id,
                "sender_psid": sender_psid,
                "recipient_psid": recipient_psid,
                "timestamp": timestamp,
                "message_type": "",
                "message_text": "",
                "message_attachments": [],
                "postback_payload": "",
            }

            if "message" in messaging:
                msg = messaging["message"]
                if "text" in msg:
                    event["message_type"] = "text"
                    event["message_text"] = msg["text"]
                if "attachments" in msg:
                    event["message_type"] = event["message_type"] or "attachment"
                    event["message_attachments"] = msg["attachments"]
                if "quick_reply" in msg:
                    event["postback_payload"] = msg["quick_reply"].get("payload", "")
            elif "postback" in messaging:
                event["message_type"] = "postback"
                event["postback_payload"] = messaging["postback"].get("payload", "")
            elif "delivery" in messaging:
                event["message_type"] = "delivery"
            elif "read" in messaging:
                event["message_type"] = "read"

            if event["message_type"]:
                events.append(event)

    return events
