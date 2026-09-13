"""WhatsApp Business Cloud API client.

Wraps the WhatsApp Business Cloud API (Meta Graph API) for:
- Sending text, media, and template messages
- Receiving webhook events (incoming messages, status updates)
- Managing business profile (about, profile photo, messaging window)
- Phone number verification

A WhatsApp "account" in SocialAuto is a WhatsApp Business phone number
registered to the Meta app. The phone number ID and access token are stored
in ``social_accounts.meta_data`` (platform="whatsapp").

API reference:
- https://developers.facebook.com/docs/whatsapp/cloud-api
- https://developers.facebook.com/docs/whatsapp/cloud-api/get-started
"""
from __future__ import annotations

import logging
import re
from typing import Any

from app.services.facebook_api import _validate_id
from app.services.meta_graph import FACEBOOK_GRAPH_VERSION
from app.services.whatsapp_cloud_client import (
    WhatsAppCloudClient,
    WhatsAppPhoneVerificationError,
    validate_wa_phone,
)

# WhatsApp Business Platform Cloud API is built on the Meta Graph API and uses
# the same `graph.facebook.com/<API_VERSION>/...` scheme. Meta docs show v26.0
# in current examples (e.g. media upload):
# https://developers.facebook.com/documentation/business-messaging/whatsapp/business-phone-numbers/media
WHATSAPP_CLOUD_API_VERSION = FACEBOOK_GRAPH_VERSION

# Backward-compatible alias used by existing callers.
DEFAULT_API_VERSION = WHATSAPP_CLOUD_API_VERSION
DEFAULT_TIMEOUT = 60.0

logger = logging.getLogger(__name__)

def _validate_phone(value: str) -> str:
    """Validate a phone number (E.164 or local format)."""
    return validate_wa_phone(value)


class WhatsAppAPIClient:
    """Async WhatsApp Business Cloud API client.

    Requires a System User access token with ``whatsapp_business_messaging``
    permission and a WhatsApp Business phone number ID.
    """

    def __init__(
        self,
        access_token: str,
        phone_number_id: str,
        api_version: str = DEFAULT_API_VERSION,
        business_phone: str | None = None,
    ):
        if not access_token:
            raise ValueError("Access token is required")
        self.phone_number_id = _validate_id(phone_number_id, "phone_number_id")
        self.api_version = api_version.lstrip("/")
        self.business_phone = business_phone or ""
        self._client = WhatsAppCloudClient(access_token=access_token, api_version=self.api_version, timeout_s=DEFAULT_TIMEOUT)

    # ------------------------------------------------------------------
    # Send API
    # ------------------------------------------------------------------

    async def send_text(
        self,
        to: str,
        text: str,
        preview_url: bool = False,
        messaging_type: str = "RESPONSE",
    ) -> dict:
        """Send a text message to a WhatsApp number.

        POST /{phone_number_id}/messages

        Args:
            to: Recipient phone number (E.164, with or without +).
            text: Message text.
            preview_url: Whether to render URL previews.
            messaging_type: "RESPONSE" (24h window), "UPDATE" (template),
                           or "MESSAGE_TAG" (tagged message).
        """
        phone = _validate_phone(to)
        body: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone,
            "type": "text",
            "text": {"body": text, "preview_url": preview_url},
        }
        return await self._client.request(
            "POST",
            f"{self.phone_number_id}/messages",
            json_body=body,
            headers={"Content-Type": "application/json"},
        )

    async def send_template(
        self,
        to: str,
        template_name: str,
        language_code: str = "en",
        components: list[dict] | None = None,
    ) -> dict:
        """Send a template message (required for initiating conversations).

        POST /{phone_number_id}/messages with type=template.
        """
        phone = _validate_phone(to)
        template: dict[str, Any] = {
            "name": template_name,
            "language": {"code": language_code},
        }
        if components:
            template["components"] = components
        body = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone,
            "type": "template",
            "template": template,
        }
        return await self._client.request(
            "POST",
            f"{self.phone_number_id}/messages",
            json_body=body,
            headers={"Content-Type": "application/json"},
        )

    async def send_image(
        self,
        to: str,
        image_url: str,
        caption: str | None = None,
    ) -> dict:
        """Send an image by URL."""
        phone = _validate_phone(to)
        media: dict[str, Any] = {"link": image_url}
        if caption:
            media["caption"] = caption
        body = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone,
            "type": "image",
            "image": media,
        }
        return await self._client.request(
            "POST",
            f"{self.phone_number_id}/messages",
            json_body=body,
            headers={"Content-Type": "application/json"},
        )

    async def send_document(
        self,
        to: str,
        document_url: str,
        filename: str | None = None,
        caption: str | None = None,
    ) -> dict:
        """Send a document (PDF, etc.) by URL."""
        phone = _validate_phone(to)
        media: dict[str, Any] = {"link": document_url}
        if filename:
            media["filename"] = filename
        if caption:
            media["caption"] = caption
        body = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone,
            "type": "document",
            "document": media,
        }
        return await self._client.request(
            "POST",
            f"{self.phone_number_id}/messages",
            json_body=body,
            headers={"Content-Type": "application/json"},
        )

    async def send_reaction(
        self,
        to: str,
        message_id: str,
        emoji: str,
    ) -> dict:
        """Send a reaction to a message."""
        phone = _validate_phone(to)
        body = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone,
            "type": "reaction",
            "reaction": {"message_id": message_id, "emoji": emoji},
        }
        return await self._client.request(
            "POST",
            f"{self.phone_number_id}/messages",
            json_body=body,
            headers={"Content-Type": "application/json"},
        )

    async def send_location(
        self,
        to: str,
        latitude: float,
        longitude: float,
        name: str | None = None,
        address: str | None = None,
    ) -> dict:
        """Send a location message."""
        phone = _validate_phone(to)
        location: dict[str, Any] = {"latitude": latitude, "longitude": longitude}
        if name:
            location["name"] = name
        if address:
            location["address"] = address
        body = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone,
            "type": "location",
            "location": location,
        }
        return await self._client.request(
            "POST",
            f"{self.phone_number_id}/messages",
            json_body=body,
            headers={"Content-Type": "application/json"},
        )

    async def mark_message_read(self, message_id: str) -> dict:
        """Mark a message as read."""
        body = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        }
        return await self._client.request(
            "POST",
            f"{self.phone_number_id}/messages",
            json_body=body,
            headers={"Content-Type": "application/json"},
        )

    # ------------------------------------------------------------------
    # Media API
    # ------------------------------------------------------------------

    async def upload_media(self, file_path: str, mime_type: str = "image/jpeg") -> dict:
        """Upload a media file to WhatsApp servers (for sending by ID).

        Returns {"id": "<media_id>"}.
        """
        with open(file_path, "rb") as f:
            return await self._client.request(
                "POST",
                f"{self.phone_number_id}/media",
                data={"messaging_product": "whatsapp", "type": mime_type},
                files={"file": (file_path.split("/")[-1], f, mime_type)},
            )

    async def download_media(self, media_id: str) -> bytes:
        """Download a media file by its ID."""
        media_info = await self._client.request("GET", _validate_id(media_id, "media_id"))
        media_url = media_info.get("url", "")
        if not media_url:
            raise ValueError("No media URL in response")
        return await self._client.get_bytes(absolute_url=str(media_url))

    # ------------------------------------------------------------------
    # Business profile
    # ------------------------------------------------------------------

    async def get_business_profile(self) -> dict:
        """Get the WhatsApp Business profile (about, photo, address, etc.)."""
        data = await self._client.request(
            "GET",
            f"{self.phone_number_id}/whatsapp_business_profile",
            params={"fields": "about,profile_picture_url,address,description,email,websites,vertical"},
        )
        if "data" in data and data["data"]:
            return data["data"][0]
        return data

    async def update_business_profile(self, updates: dict) -> dict:
        """Update the WhatsApp Business profile fields.

        Allowed fields: about, address, description, email, websites, vertical.
        """
        body = {
            "messaging_product": "whatsapp",
            **{k: v for k, v in updates.items() if v is not None},
        }
        return await self._client.request(
            "POST",
            f"{self.phone_number_id}/whatsapp_business_profile",
            json_body=body,
            headers={"Content-Type": "application/json"},
        )

    # ------------------------------------------------------------------
    # Phone number info
    # ------------------------------------------------------------------

    async def get_phone_number_info(self) -> dict:
        """Get info about the WhatsApp Business phone number."""
        return await self.get_phone_number_info_by_id(self.phone_number_id)

    async def get_phone_number_info_by_id(self, phone_number_id: str) -> dict:
        """Get info about any WhatsApp Business phone number id."""
        pnid = _validate_id(phone_number_id, "phone_number_id")
        try:
            return await self._client.request(
                "GET",
                pnid,
                params={"fields": "display_phone_number,verified_name,quality_rating,code_verification_status"},
            )
        except Exception:
            # Some accounts/tenants intermittently reject certain fields; fall back to the raw object payload.
            return await self._client.request("GET", pnid)

    async def get_business_account_phone_numbers(self, business_account_id: str) -> list[dict]:
        """List all phone numbers registered to a WhatsApp Business Account."""
        bid = _validate_id(business_account_id, "business_account_id")
        data = await self._client.request(
            "GET",
            f"{bid}/phone_numbers",
            params={"fields": "display_phone_number,verified_name,quality_rating,id"},
        )
        return data.get("data", [])

    # ------------------------------------------------------------------
    # Phone number registration (4-step flow)
    # https://developers.facebook.com/docs/whatsapp/cloud-api/get-started/registering-phone-numbers
    # ------------------------------------------------------------------

    async def create_phone_number(
        self,
        waba_id: str,
        cc: str,
        phone_number: str,
        verified_name: str,
    ) -> dict:
        """Step 1: Create a business phone number on a WABA.

        POST /{waba_id}/phone_numbers

        Args:
            waba_id: WhatsApp Business Account ID.
            cc: Country calling code (e.g. "1", "30").
            phone_number: Phone number without country code (e.g. "15551234").
            verified_name: Display name for the business.

        Returns: {"id": "<phone_number_id>"}
        """
        waba = _validate_id(waba_id, "waba_id")
        body = {
            "cc": cc,
            "phone_number": phone_number,
            "verified_name": verified_name,
        }
        return await self._client.request(
            "POST",
            f"{waba}/phone_numbers",
            json_body=body,
            headers={"Content-Type": "application/json"},
        )

    async def request_verification_code(
        self,
        phone_number_id: str,
        code_method: str = "SMS",
        language: str = "en_US",
    ) -> dict:
        """Step 2: Request a verification code sent to the business phone number.

        POST /{phone_number_id}/request_code?code_method=SMS&language=en_US

        Args:
            phone_number_id: The phone number ID from step 1.
            code_method: "SMS" or "VOICE".
            language: Language code (e.g. "en_US", "el_GR").

        Returns: {"success": true}
        """
        pnid = _validate_id(phone_number_id, "phone_number_id")
        if code_method not in ("SMS", "VOICE"):
            raise ValueError("code_method must be 'SMS' or 'VOICE'")
        # Meta docs: if already verified, request_code returns 400 with code 136024.
        # Avoid hammering the endpoint by checking status first.
        try:
            info = await self.get_phone_number_info_by_id(pnid)
            if info.get("code_verification_status") == "VERIFIED":
                return {
                    "success": True,
                    "skipped": True,
                    "reason": "already_verified",
                    "phone_number_id": pnid,
                    "code_verification_status": "VERIFIED",
                }
        except Exception:
            # If the status check fails, proceed with the request; we still surface 136024 clearly below.
            info = {}

        try:
            return await self._client.request(
                "POST",
                f"{pnid}/request_code",
                params={"code_method": code_method, "language": language},
            )
        except WhatsAppPhoneVerificationError as e:
            # Make 136024 actionable in logs and user-facing errors.
            raise WhatsAppPhoneVerificationError(
                status_code=e.status_code,
                url=e.url,
                response_text=e.response_text,
                error=e.error,
                rate_limit=e.rate_limit,
            ) from e

    async def verify_code(self, phone_number_id: str, code: str) -> dict:
        """Step 3: Verify the business phone number with the code.

        POST /{phone_number_id}/verify_code?code=123830

        Args:
            phone_number_id: The phone number ID from step 1.
            code: Verification code received via SMS/voice, without hyphen.

        Returns: {"success": true}
        """
        pnid = _validate_id(phone_number_id, "phone_number_id")
        # Strip any hyphens/spaces from the code
        clean_code = re.sub(r"[\s\-]", "", code)
        if not clean_code.isdigit():
            raise ValueError("Verification code must be numeric")
        return await self._client.request(
            "POST",
            f"{pnid}/verify_code",
            params={"code": clean_code},
        )

    async def register_number(self, phone_number_id: str, pin: str) -> dict:
        """Step 4: Register the verified phone number for API use.

        POST /{phone_number_id}/register

        Args:
            phone_number_id: The verified phone number ID.
            pin: 6-digit two-step verification PIN.

        Returns: {"success": true}
        """
        pnid = _validate_id(phone_number_id, "phone_number_id")
        clean_pin = re.sub(r"[\s\-]", "", pin)
        if not (clean_pin.isdigit() and len(clean_pin) == 6):
            raise ValueError("PIN must be exactly 6 digits")
        body = {
            "messaging_product": "whatsapp",
            "pin": clean_pin,
        }
        return await self._client.request(
            "POST",
            f"{pnid}/register",
            json_body=body,
            headers={"Content-Type": "application/json"},
        )

    async def deregister_number(self, phone_number_id: str) -> dict:
        """Deregister a business phone number (stops API use).

        POST /{phone_number_id}/deregister
        """
        pnid = _validate_id(phone_number_id, "phone_number_id")
        return await self._client.request("POST", f"{pnid}/deregister")

    # ------------------------------------------------------------------
    # WABA webhook subscriptions (Subscribed Apps API)
    # https://developers.facebook.com/docs/whatsapp/embedded-signup/webhooks
    # ------------------------------------------------------------------

    async def subscribe_app_to_waba(self, waba_id: str) -> dict:
        """Subscribe the app to webhooks on a WABA.

        POST /{waba_id}/subscribed_apps

        After fetching the client's WABA ID, subscribe your app to the ID
        to start receiving webhooks for that WABA. Webhook notifications are
        sent to the app's callback URL configured in the App Dashboard.

        Returns: {"success": true}
        """
        waba = _validate_id(waba_id, "waba_id")
        return await self._client.request("POST", f"{waba}/subscribed_apps")

    async def list_waba_subscriptions(self, waba_id: str) -> list[dict]:
        """List all apps subscribed to webhooks on a WABA.

        GET /{waba_id}/subscribed_apps

        Returns an array of apps with ``id``, ``link``, and ``name`` properties
        for each subscribed app.
        """
        waba = _validate_id(waba_id, "waba_id")
        data = await self._client.request("GET", f"{waba}/subscribed_apps")
        items = data.get("data", [])
        return [item.get("whatsapp_business_api_data", item) for item in items]

    async def unsubscribe_app_from_waba(self, waba_id: str) -> dict:
        """Unsubscribe the app from webhooks for a WABA.

        DELETE /{waba_id}/subscribed_apps

        Returns: {"success": true}
        """
        waba = _validate_id(waba_id, "waba_id")
        return await self._client.request("DELETE", f"{waba}/subscribed_apps")


# ------------------------------------------------------------------
# Webhook event helpers
# ------------------------------------------------------------------

def parse_webhook_event(body: dict) -> list[dict]:
    """Parse a WhatsApp webhook payload and return normalized events.

    Each event is a dict with:
    - phone_number_id: the business phone number that received the message
    - sender_phone: the customer's phone number
    - timestamp: event timestamp
    - message_text: text content (if any)
    - message_type: "text", "image", "audio", "video", "document", "location", "button", "interactive", "status"
    - message_id: WhatsApp message ID
    - status: delivery status (for status events)
    """
    events: list[dict] = []
    if body.get("object") != "whatsapp_business_account":
        return events

    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            phone_number_id = value.get("metadata", {}).get("phone_number_id", "")

            # Extract contact info (name, wa_id) from the contacts array
            contacts_map: dict[str, str] = {}
            for contact in value.get("contacts", []):
                wa_id = contact.get("wa_id", "")
                name = contact.get("profile", {}).get("name", "")
                if wa_id:
                    contacts_map[wa_id] = name

            # Incoming messages
            for msg in value.get("messages", []):
                sender_phone = msg.get("from", "")
                msg_type = msg.get("type", "")
                msg_id = msg.get("id", "")
                timestamp = msg.get("timestamp", "0")

                # WhatsApp timestamps are decimal Unix epoch strings (e.g. "1749416383")
                try:
                    ts_int = int(timestamp)
                except (ValueError, TypeError):
                    ts_int = 0

                event: dict[str, Any] = {
                    "phone_number_id": phone_number_id,
                    "sender_phone": sender_phone,
                    "sender_name": contacts_map.get(sender_phone, ""),
                    "message_type": msg_type,
                    "message_id": msg_id,
                    "timestamp": ts_int,
                }

                if msg_type == "text":
                    event["message_text"] = msg.get("text", {}).get("body", "")
                elif msg_type == "button":
                    event["message_text"] = msg.get("button", {}).get("text", "")
                    event["button_payload"] = msg.get("button", {}).get("payload", "")
                elif msg_type == "interactive":
                    interactive = msg.get("interactive", {})
                    itype = interactive.get("type", "")
                    if itype == "button_reply":
                        event["message_text"] = interactive.get("button_reply", {}).get("title", "")
                        event["button_payload"] = interactive.get("button_reply", {}).get("id", "")
                    elif itype == "list_reply":
                        event["message_text"] = interactive.get("list_reply", {}).get("title", "")
                        event["button_payload"] = interactive.get("list_reply", {}).get("id", "")
                elif msg_type in ("image", "audio", "video", "document"):
                    media = msg.get(msg_type, {})
                    event["media_id"] = media.get("id", "")
                    event["mime_type"] = media.get("mime_type", "")
                    if msg_type in ("image", "video", "document"):
                        event["caption"] = media.get("caption", "")
                elif msg_type == "location":
                    loc = msg.get("location", {})
                    event["latitude"] = loc.get("latitude")
                    event["longitude"] = loc.get("longitude")
                    event["address"] = loc.get("name", "") or loc.get("address", "")
                elif msg_type == "contacts":
                    # Contacts message — list of shared contact cards
                    event["contacts"] = msg.get("contacts", [])
                elif msg_type == "reaction":
                    reaction = msg.get("reaction", {})
                    event["reaction_emoji"] = reaction.get("emoji", "")
                    event["reaction_message_id"] = reaction.get("message_id", "")
                elif msg_type == "sticker":
                    sticker = msg.get("sticker", {})
                    event["media_id"] = sticker.get("id", "")
                    event["animated"] = sticker.get("animated", False)
                elif msg_type == "system":
                    event["message_text"] = msg.get("system", {}).get("body", "")
                elif msg_type == "unknown":
                    event["message_text"] = msg.get("errors", [{}])[0].get("message", "unsupported message type")

                # Context (if this message is a reply to another message)
                context = msg.get("context", {})
                if context:
                    event["context_message_id"] = context.get("id", "")
                    event["context_from"] = context.get("from", "")

                events.append(event)

            # Status updates (sent, delivered, read)
            for status in value.get("statuses", []):
                status_ts = status.get("timestamp", "0")
                try:
                    status_ts_int = int(status_ts) if status_ts else 0
                except (ValueError, TypeError):
                    status_ts_int = 0
                events.append({
                    "phone_number_id": phone_number_id,
                    "sender_phone": status.get("recipient_id", ""),
                    "message_type": "status",
                    "message_id": status.get("id", ""),
                    "status": status.get("status", ""),
                    "timestamp": status_ts_int,
                })

    return events
