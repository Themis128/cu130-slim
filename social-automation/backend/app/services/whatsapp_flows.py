"""WhatsApp Flows API client.

Wraps the WhatsApp Flows Graph API for:
- Creating, listing, updating, publishing, and deleting Flows
- Managing Flow JSON content
- Sending Flow messages to customers
- Receiving Flow response webhooks

A WhatsApp Flow is a structured interactive experience built directly inside
WhatsApp chats. Flows can be static (no backend) or endpoint-powered (calling
a data_channel_uri for dynamic data and routing).

API reference:
- https://developers.facebook.com/docs/whatsapp/flows
- https://developers.facebook.com/docs/whatsapp/flows/reference/flowsapi
- https://developers.facebook.com/docs/whatsapp/flows/reference/flowjson
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any, TypedDict

import httpx

from app.services.facebook_api import FacebookAPIError, _sanitize_log_text, _validate_id
from app.services.whatsapp_api import DEFAULT_API_VERSION, DEFAULT_TIMEOUT, _validate_phone

FACEBOOK_GRAPH_BASE = "https://graph.facebook.com"

logger = logging.getLogger(__name__)

# Valid Flow categories per Meta documentation
VALID_FLOW_CATEGORIES = frozenset({
    "SIGN_UP",
    "SIGN_IN",
    "APPOINTMENT_BOOKING",
    "LEAD_GENERATION",
    "CONTACT_US",
    "CUSTOMER_SUPPORT",
    "SURVEY",
    "OTHER",
})

# Valid Flow statuses
VALID_FLOW_STATUSES = frozenset({
    "DRAFT",
    "PUBLISHED",
    "BLOCKED",
    "DEPRECATED",
    "UNPUBLISHED",
})


class WhatsAppFlowsClient:
    """Async WhatsApp Flows API client.

    Requires a System User access token with ``whatsapp_business_management``
    permission and a WABA ID.
    """

    def __init__(
        self,
        access_token: str,
        waba_id: str,
        phone_number_id: str = "",
        api_version: str = DEFAULT_API_VERSION,
    ):
        if not access_token:
            raise ValueError("Access token is required")
        self.access_token = access_token
        self.waba_id = _validate_id(waba_id, "waba_id")
        self.phone_number_id = phone_number_id
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
        logger.error("WhatsApp Flows API error %s for %s: %s", resp.status_code, safe_url, text)
        err = FacebookAPIError.from_response(resp, url)
        if resp.status_code >= 500:
            err.status_code = 503 if resp.status_code in (503, 504) else 502
        raise err

    # ------------------------------------------------------------------
    # Flow CRUD operations
    # ------------------------------------------------------------------

    async def create_flow(
        self,
        name: str,
        categories: list[str],
        endpoint_uri: str | None = None,
        clone_flow_id: str | None = None,
    ) -> dict:
        """Create a new Flow.

        POST /{waba_id}/flows

        Args:
            name: Flow name (must be unique within the WABA).
            categories: At least one Flow category. See VALID_FLOW_CATEGORIES.
            endpoint_uri: URL of the Flow Data Endpoint (for endpoint-powered Flows).
            clone_flow_id: ID of a source Flow to clone.

        Returns: {"id": "<flow_id>"}
        """
        if not name:
            raise ValueError("Flow name is required")
        if not categories:
            raise ValueError("At least one category is required")
        for cat in categories:
            if cat not in VALID_FLOW_CATEGORIES:
                raise ValueError(
                    f"Invalid category '{cat}'. Valid: {sorted(VALID_FLOW_CATEGORIES)}"
                )

        body: dict[str, Any] = {
            "name": name,
            "categories": categories,
        }
        if endpoint_uri:
            body["endpoint_uri"] = endpoint_uri
        if clone_flow_id:
            body["clone_flow_id"] = clone_flow_id

        url = self._url(f"{self.waba_id}/flows")
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(
                url,
                params=self._params(),
                json=body,
                headers={"Content-Type": "application/json"},
            )
            self._raise_for_status(resp, url)
            return resp.json()

    async def list_flows(self, fields: list[str] | None = None) -> list[dict]:
        """List all Flows on the WABA.

        GET /{waba_id}/flows

        Returns a list of Flow objects with id, name, status, categories, etc.
        """
        default_fields = ["id", "name", "status", "categories", "validation_errors"]
        req_fields = fields or default_fields
        url = self._url(f"{self.waba_id}/flows")
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(
                url,
                params=self._params({"fields": ",".join(req_fields)}),
            )
            self._raise_for_status(resp, url)
            return resp.json().get("data", [])

    async def get_flow(
        self,
        flow_id: str,
        fields: list[str] | None = None,
    ) -> dict:
        """Get details of a single Flow.

        GET /{flow_id}

        By default returns id, name, status, categories, validation_errors.
        """
        fid = _validate_id(flow_id, "flow_id")
        default_fields = [
            "id", "name", "status", "categories", "validation_errors",
            "endpoint_uri", "flow_validation_version", "data_api_version",
        ]
        req_fields = fields or default_fields
        url = self._url(fid)
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(
                url,
                params=self._params({"fields": ",".join(req_fields)}),
            )
            self._raise_for_status(resp, url)
            return resp.json()

    async def update_flow_metadata(
        self,
        flow_id: str,
        name: str | None = None,
        categories: list[str] | None = None,
        endpoint_uri: str | None = None,
    ) -> dict:
        """Update a Flow's metadata (name, categories, endpoint_uri).

        POST /{flow_id}

        Args:
            flow_id: Flow ID.
            name: New Flow name.
            categories: New categories list.
            endpoint_uri: New endpoint URL (for endpoint-powered Flows).

        Returns: {"success": true}
        """
        fid = _validate_id(flow_id, "flow_id")
        body: dict[str, Any] = {}
        if name:
            body["name"] = name
        if categories:
            for cat in categories:
                if cat not in VALID_FLOW_CATEGORIES:
                    raise ValueError(
                        f"Invalid category '{cat}'. Valid: {sorted(VALID_FLOW_CATEGORIES)}"
                    )
            body["categories"] = categories
        if endpoint_uri is not None:
            body["endpoint_uri"] = endpoint_uri

        if not body:
            raise ValueError("At least one field to update is required")

        url = self._url(fid)
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(
                url,
                params=self._params(),
                json=body,
                headers={"Content-Type": "application/json"},
            )
            self._raise_for_status(resp, url)
            return resp.json()

    async def update_flow_json(
        self,
        flow_id: str,
        flow_json: dict | str,
    ) -> dict:
        """Update a Flow's Flow JSON content.

        POST /{flow_id}/flow_json

        Args:
            flow_id: Flow ID.
            flow_json: The Flow JSON object (or its string representation).

        Returns: {"success": true}
        """
        fid = _validate_id(flow_id, "flow_id")
        if isinstance(flow_json, dict):
            flow_json_str = json.dumps(flow_json)
        else:
            flow_json_str = flow_json

        url = self._url(f"{fid}/flow_json")
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(
                url,
                params=self._params(),
                json={"flow_json": flow_json_str},
                headers={"Content-Type": "application/json"},
            )
            self._raise_for_status(resp, url)
            return resp.json()

    async def publish_flow(self, flow_id: str) -> dict:
        """Publish a Flow (makes it live, cannot be edited after).

        POST /{flow_id}/publish

        Returns: {"success": true}
        """
        fid = _validate_id(flow_id, "flow_id")
        url = self._url(f"{fid}/publish")
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(
                url,
                params=self._params(),
                headers={"Content-Type": "application/json"},
            )
            self._raise_for_status(resp, url)
            return resp.json()

    async def delete_flow(self, flow_id: str) -> dict:
        """Delete a Flow.

        DELETE /{flow_id}

        Returns: {"success": true}
        """
        fid = _validate_id(flow_id, "flow_id")
        url = self._url(fid)
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.delete(url, params=self._params())
            self._raise_for_status(resp, url)
            return resp.json()

    async def get_flow_json(self, flow_id: str) -> dict:
        """Get the Flow JSON content of a Flow.

        GET /{flow_id}/flow_json

        Returns the Flow JSON as a dict.
        """
        fid = _validate_id(flow_id, "flow_id")
        url = self._url(f"{fid}/flow_json")
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(url, params=self._params())
            self._raise_for_status(resp, url)
            return resp.json()

    async def validate_flow_json(self, flow_id: str, flow_json: dict | str) -> dict:
        """Validate a Flow JSON without saving it.

        POST /{flow_id}/validate

        Returns validation result with any errors.
        """
        fid = _validate_id(flow_id, "flow_id")
        if isinstance(flow_json, dict):
            flow_json_str = json.dumps(flow_json)
        else:
            flow_json_str = flow_json

        url = self._url(f"{fid}/validate")
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(
                url,
                params=self._params(),
                json={"flow_json": flow_json_str},
                headers={"Content-Type": "application/json"},
            )
            self._raise_for_status(resp, url)
            return resp.json()

    # ------------------------------------------------------------------
    # Send Flow message
    # ------------------------------------------------------------------

    async def send_flow(
        self,
        to: str,
        flow_id: str,
        flow_token: str,
        body_text: str = "",
        header_text: str | None = None,
        footer_text: str | None = None,
        flow_action: str = "navigate",
        screen: str = "FIRST_ENTRY_SCREEN",
        flow_action_payload: dict | None = None,
        messaging_type: str = "RESPONSE",
    ) -> dict:
        """Send a Flow message to a WhatsApp number.

        POST /{phone_number_id}/messages with type=interactive, interactive.type=flow

        Args:
            to: Recipient phone number (E.164).
            flow_id: The Flow ID to send.
            flow_token: Unique token for tracking this Flow message (business-defined).
            body_text: Body text shown below the Flow header.
            header_text: Optional header text (shown above body).
            footer_text: Optional footer text (shown below body).
            flow_action: "navigate" (open a screen) or "data_exchange" (endpoint-powered).
            screen: Target screen for "navigate" action.
            flow_action_payload: Additional payload for the action.
            messaging_type: "RESPONSE" (24h window) or "TEMPLATE" (business-initiated).

        Returns: {"message_id": "...", "status": "sent"}
        """
        if not self.phone_number_id:
            raise ValueError("phone_number_id is required to send Flow messages")
        phone = _validate_phone(to)
        fid = _validate_id(flow_id, "flow_id")

        if flow_action not in ("navigate", "data_exchange"):
            raise ValueError("flow_action must be 'navigate' or 'data_exchange'")

        action_payload: dict[str, Any] = {
            "flow_id": fid,
            "flow_token": flow_token,
            "flow_action": flow_action,
        }
        if flow_action == "navigate":
            payload = flow_action_payload or {}
            if screen:
                payload["screen"] = screen
            action_payload["flow_action_payload"] = payload
        else:
            # data_exchange — pass payload directly
            if flow_action_payload:
                action_payload["flow_action_payload"] = flow_action_payload

        interactive: dict[str, Any] = {
            "type": "flow",
            "body": {"text": body_text},
            "action": {
                "name": "flow",
                "parameters": action_payload,
            },
        }
        if header_text:
            interactive["header"] = {"type": "text", "text": header_text}
        if footer_text:
            interactive["footer"] = {"text": footer_text}

        body = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone,
            "type": "interactive",
            "interactive": interactive,
        }
        if messaging_type:
            body["messaging_type"] = messaging_type

        url = self._url(f"{self.phone_number_id}/messages")
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
# Flow JSON templates
# ------------------------------------------------------------------

def lead_generation_flow_json(business_name: str = "Cloudless") -> dict:
    """Generate a Lead Generation Flow JSON for Cloudless.

    This Flow collects name, email, phone, and service interest from prospects.
    No endpoint required — responses come through the webhook.
    """
    return {
        "version": "5.0",
        "data_api_version": "3.0",
        "routing_model": {
            "FIRST_ENTRY_SCREEN": ["LEAD_FORM"],
            "LEAD_FORM": ["SUCCESS_SCREEN", "FIRST_ENTRY_SCREEN"],
            "SUCCESS_SCREEN": [],
        },
        "screens": {
            "FIRST_ENTRY_SCREEN": {
                "title": business_name,
                "data": {},
                "layout": {
                    "type": "SingleColumnLayout",
                    "children": [
                        {
                            "type": "TextHeading",
                            "text": f"Welcome to {business_name}",
                        },
                        {
                            "type": "TextBody",
                            "text": "We provide open-source cloud solutions and social media automation. Fill out the form below and we'll get back to you.",
                        },
                        {
                            "type": "Footer",
                            "label": "Get Started",
                            "on-click-action": {
                                "name": "navigate",
                                "next": "LEAD_FORM",
                            },
                        },
                    ],
                },
            },
            "LEAD_FORM": {
                "title": "Contact Details",
                "data": {
                    "name": "",
                    "email": "",
                    "phone": "",
                    "service_interest": "",
                    "message": "",
                },
                "layout": {
                    "type": "SingleColumnLayout",
                    "children": [
                        {
                            "type": "Form",
                            "children": [
                                {
                                    "type": "TextInput",
                                    "label": "Full Name",
                                    "required": True,
                                    "name": "name",
                                },
                                {
                                    "type": "TextInput",
                                    "label": "Email",
                                    "input-type": "email",
                                    "required": True,
                                    "name": "email",
                                },
                                {
                                    "type": "TextInput",
                                    "label": "Phone Number",
                                    "input-type": "phone",
                                    "name": "phone",
                                },
                                {
                                    "type": "Dropdown",
                                    "label": "Service Interest",
                                    "name": "service_interest",
                                    "data-source": [
                                        {"id": "cloud", "title": "Cloud Solutions"},
                                        {"id": "automation", "title": "Social Media Automation"},
                                        {"id": "ai", "title": "AI Content Creation"},
                                        {"id": "consulting", "title": "Consulting"},
                                        {"id": "other", "title": "Other"},
                                    ],
                                },
                                {
                                    "type": "TextArea",
                                    "label": "Message (optional)",
                                    "name": "message",
                                    "helper-text": "Tell us about your project",
                                },
                                {
                                    "type": "Footer",
                                    "label": "Submit",
                                    "on-click-action": {
                                        "name": "complete",
                                        "payload": {
                                            "name": "${form.name}",
                                            "email": "${form.email}",
                                            "phone": "${form.phone}",
                                            "service_interest": "${form.service_interest}",
                                            "message": "${form.message}",
                                        },
                                    },
                                },
                            ],
                        },
                    ],
                },
            },
            "SUCCESS_SCREEN": {
                "title": "Thank You",
                "data": {},
                "layout": {
                    "type": "SingleColumnLayout",
                    "children": [
                        {
                            "type": "TextHeading",
                            "text": "Thank You!",
                        },
                        {
                            "type": "TextBody",
                            "text": f"We've received your message and will get back to you within 24 hours. — {business_name}",
                        },
                    ],
                },
            },
        },
    }


def customer_support_flow_json(business_name: str = "Cloudless") -> dict:
    """Generate a Customer Support Flow JSON for Cloudless.

    This Flow collects issue type, priority, and description for support tickets.
    No endpoint required — responses come through the webhook.
    """
    return {
        "version": "5.0",
        "routing_model": {
            "FIRST_ENTRY_SCREEN": ["SUPPORT_FORM"],
            "SUPPORT_FORM": ["SUCCESS_SCREEN", "FIRST_ENTRY_SCREEN"],
            "SUCCESS_SCREEN": [],
        },
        "screens": {
            "FIRST_ENTRY_SCREEN": {
                "title": f"{business_name} Support",
                "data": {},
                "layout": {
                    "type": "SingleColumnLayout",
                    "children": [
                        {
                            "type": "TextHeading",
                            "text": "How can we help?",
                        },
                        {
                            "type": "TextBody",
                            "text": "Fill out the form below and our support team will respond shortly.",
                        },
                        {
                            "type": "Footer",
                            "label": "Get Started",
                            "on-click-action": {
                                "name": "navigate",
                                "next": "SUPPORT_FORM",
                            },
                        },
                    ],
                },
            },
            "SUPPORT_FORM": {
                "title": "Support Request",
                "data": {
                    "issue_type": "",
                    "priority": "",
                    "description": "",
                },
                "layout": {
                    "type": "SingleColumnLayout",
                    "children": [
                        {
                            "type": "Form",
                            "children": [
                                {
                                    "type": "Dropdown",
                                    "label": "Issue Type",
                                    "name": "issue_type",
                                    "required": True,
                                    "data-source": [
                                        {"id": "bug", "title": "Bug Report"},
                                        {"id": "billing", "title": "Billing"},
                                        {"id": "technical", "title": "Technical Issue"},
                                        {"id": "account", "title": "Account Access"},
                                        {"id": "feature", "title": "Feature Request"},
                                        {"id": "other", "title": "Other"},
                                    ],
                                },
                                {
                                    "type": "RadioButtonsGroup",
                                    "label": "Priority",
                                    "name": "priority",
                                    "required": True,
                                    "data-source": [
                                        {"id": "low", "title": "Low"},
                                        {"id": "medium", "title": "Medium"},
                                        {"id": "high", "title": "High"},
                                        {"id": "urgent", "title": "Urgent"},
                                    ],
                                },
                                {
                                    "type": "TextArea",
                                    "label": "Describe the issue",
                                    "name": "description",
                                    "required": True,
                                    "helper-text": "Please provide as much detail as possible",
                                },
                                {
                                    "type": "Footer",
                                    "label": "Submit",
                                    "on-click-action": {
                                        "name": "complete",
                                        "payload": {
                                            "issue_type": "${form.issue_type}",
                                            "priority": "${form.priority}",
                                            "description": "${form.description}",
                                        },
                                    },
                                },
                            ],
                        },
                    ],
                },
            },
            "SUCCESS_SCREEN": {
                "title": "Submitted",
                "data": {},
                "layout": {
                    "type": "SingleColumnLayout",
                    "children": [
                        {
                            "type": "TextHeading",
                            "text": "Request Submitted",
                        },
                        {
                            "type": "TextBody",
                            "text": f"Thank you! Our support team will review your request and respond within 24 hours. — {business_name}",
                        },
                    ],
                },
            },
        },
    }


def appointment_booking_flow_json(business_name: str = "Cloudless") -> dict:
    """Generate an Appointment Booking Flow JSON for Cloudless.

    This Flow collects preferred date, time slot, and contact info for consultations.
    No endpoint required — responses come through the webhook.
    """
    return {
        "version": "5.0",
        "routing_model": {
            "FIRST_ENTRY_SCREEN": ["BOOKING_FORM"],
            "BOOKING_FORM": ["SUCCESS_SCREEN", "FIRST_ENTRY_SCREEN"],
            "SUCCESS_SCREEN": [],
        },
        "screens": {
            "FIRST_ENTRY_SCREEN": {
                "title": f"Book with {business_name}",
                "data": {},
                "layout": {
                    "type": "SingleColumnLayout",
                    "children": [
                        {
                            "type": "TextHeading",
                            "text": "Schedule a Consultation",
                        },
                        {
                            "type": "TextBody",
                            "text": "Book a free 30-minute consultation to discuss your cloud, automation, or AI needs.",
                        },
                        {
                            "type": "Footer",
                            "label": "Book Now",
                            "on-click-action": {
                                "name": "navigate",
                                "next": "BOOKING_FORM",
                            },
                        },
                    ],
                },
            },
            "BOOKING_FORM": {
                "title": "Booking Details",
                "data": {
                    "name": "",
                    "email": "",
                    "preferred_date": "",
                    "time_slot": "",
                    "topic": "",
                },
                "layout": {
                    "type": "SingleColumnLayout",
                    "children": [
                        {
                            "type": "Form",
                            "children": [
                                {
                                    "type": "TextInput",
                                    "label": "Full Name",
                                    "required": True,
                                    "name": "name",
                                },
                                {
                                    "type": "TextInput",
                                    "label": "Email",
                                    "input-type": "email",
                                    "required": True,
                                    "name": "email",
                                },
                                {
                                    "type": "DatePicker",
                                    "label": "Preferred Date",
                                    "required": True,
                                    "name": "preferred_date",
                                },
                                {
                                    "type": "RadioButtonsGroup",
                                    "label": "Time Slot",
                                    "name": "time_slot",
                                    "required": True,
                                    "data-source": [
                                        {"id": "morning", "title": "Morning (09:00-12:00)"},
                                        {"id": "afternoon", "title": "Afternoon (12:00-15:00)"},
                                        {"id": "evening", "title": "Evening (15:00-18:00)"},
                                    ],
                                },
                                {
                                    "type": "Dropdown",
                                    "label": "Topic",
                                    "name": "topic",
                                    "data-source": [
                                        {"id": "cloud", "title": "Cloud Solutions"},
                                        {"id": "automation", "title": "Social Media Automation"},
                                        {"id": "ai", "title": "AI Content Creation"},
                                        {"id": "general", "title": "General Inquiry"},
                                    ],
                                },
                                {
                                    "type": "Footer",
                                    "label": "Confirm Booking",
                                    "on-click-action": {
                                        "name": "complete",
                                        "payload": {
                                            "name": "${form.name}",
                                            "email": "${form.email}",
                                            "preferred_date": "${form.preferred_date}",
                                            "time_slot": "${form.time_slot}",
                                            "topic": "${form.topic}",
                                        },
                                    },
                                },
                            ],
                        },
                    ],
                },
            },
            "SUCCESS_SCREEN": {
                "title": "Booking Confirmed",
                "data": {},
                "layout": {
                    "type": "SingleColumnLayout",
                    "children": [
                        {
                            "type": "TextHeading",
                            "text": "Booking Received!",
                        },
                        {
                            "type": "TextBody",
                            "text": f"We'll confirm your appointment via email within 2 hours. Looking forward to speaking with you! — {business_name}",
                        },
                    ],
                },
            },
        },
    }


def feedback_survey_flow_json(business_name: str = "Cloudless") -> dict:
    """Generate a Customer Feedback Survey Flow JSON for Cloudless.

    This Flow collects satisfaction ratings and open feedback.
    No endpoint required — responses come through the webhook.
    """
    return {
        "version": "5.0",
        "routing_model": {
            "FIRST_ENTRY_SCREEN": ["SURVEY_FORM"],
            "SURVEY_FORM": ["SUCCESS_SCREEN", "FIRST_ENTRY_SCREEN"],
            "SUCCESS_SCREEN": [],
        },
        "screens": {
            "FIRST_ENTRY_SCREEN": {
                "title": f"{business_name} Feedback",
                "data": {},
                "layout": {
                    "type": "SingleColumnLayout",
                    "children": [
                        {
                            "type": "TextHeading",
                            "text": "We'd Love Your Feedback",
                        },
                        {
                            "type": "TextBody",
                            "text": "Your feedback helps us improve our services. It takes less than a minute.",
                        },
                        {
                            "type": "Footer",
                            "label": "Start",
                            "on-click-action": {
                                "name": "navigate",
                                "next": "SURVEY_FORM",
                            },
                        },
                    ],
                },
            },
            "SURVEY_FORM": {
                "title": "Survey",
                "data": {
                    "satisfaction": "",
                    "recommend": "",
                    "improvement": "",
                },
                "layout": {
                    "type": "SingleColumnLayout",
                    "children": [
                        {
                            "type": "Form",
                            "children": [
                                {
                                    "type": "RadioButtonsGroup",
                                    "label": "How satisfied are you with our services?",
                                    "name": "satisfaction",
                                    "required": True,
                                    "data-source": [
                                        {"id": "very_satisfied", "title": "Very Satisfied"},
                                        {"id": "satisfied", "title": "Satisfied"},
                                        {"id": "neutral", "title": "Neutral"},
                                        {"id": "dissatisfied", "title": "Dissatisfied"},
                                        {"id": "very_dissatisfied", "title": "Very Dissatisfied"},
                                    ],
                                },
                                {
                                    "type": "RadioButtonsGroup",
                                    "label": "Would you recommend us?",
                                    "name": "recommend",
                                    "required": True,
                                    "data-source": [
                                        {"id": "yes", "title": "Yes"},
                                        {"id": "maybe", "title": "Maybe"},
                                        {"id": "no", "title": "No"},
                                    ],
                                },
                                {
                                    "type": "TextArea",
                                    "label": "What can we improve?",
                                    "name": "improvement",
                                    "helper-text": "Your suggestions are valuable to us",
                                },
                                {
                                    "type": "Footer",
                                    "label": "Submit",
                                    "on-click-action": {
                                        "name": "complete",
                                        "payload": {
                                            "satisfaction": "${form.satisfaction}",
                                            "recommend": "${form.recommend}",
                                            "improvement": "${form.improvement}",
                                        },
                                    },
                                },
                            ],
                        },
                    ],
                },
            },
            "SUCCESS_SCREEN": {
                "title": "Thank You",
                "data": {},
                "layout": {
                    "type": "SingleColumnLayout",
                    "children": [
                        {
                            "type": "TextHeading",
                            "text": "Thank You!",
                        },
                        {
                            "type": "TextBody",
                            "text": f"We appreciate your feedback. It helps us serve you better. — {business_name}",
                        },
                    ],
                },
            },
        },
    }


# Template registry
class FlowTemplate(TypedDict):
    """Metadata + JSON generator for a pre-built WhatsApp Flow template."""

    name: str
    category: str
    description: str
    generator: Callable[..., dict]


FLOW_TEMPLATES: dict[str, FlowTemplate] = {
    "lead_generation": {
        "name": "Lead Generation",
        "category": "LEAD_GENERATION",
        "description": "Collect name, email, phone, and service interest from prospects",
        "generator": lead_generation_flow_json,
    },
    "customer_support": {
        "name": "Customer Support",
        "category": "CUSTOMER_SUPPORT",
        "description": "Collect issue type, priority, and description for support tickets",
        "generator": customer_support_flow_json,
    },
    "appointment_booking": {
        "name": "Appointment Booking",
        "category": "APPOINTMENT_BOOKING",
        "description": "Book consultations with date, time slot, and topic selection",
        "generator": appointment_booking_flow_json,
    },
    "feedback_survey": {
        "name": "Feedback Survey",
        "category": "SURVEY",
        "description": "Collect satisfaction ratings and improvement suggestions",
        "generator": feedback_survey_flow_json,
    },
}


# ------------------------------------------------------------------
# Flow webhook response parsing
# ------------------------------------------------------------------

def parse_flow_response(body: dict) -> list[dict]:
    """Parse Flow response messages from a WhatsApp webhook payload.

    When a user completes a Flow, the response arrives as an interactive
    message with type "nfm_context" (Native Flow Message context).

    Returns a list of Flow response events, each with:
    - phone_number_id: business phone number ID
    - sender_phone: customer's phone number
    - flow_token: the token the business sent with the Flow
    - flow_id: the Flow ID
    - response_json: parsed response data (dict)
    - message_id: WhatsApp message ID
    - timestamp: event timestamp
    """
    events: list[dict] = []
    if body.get("object") != "whatsapp_business_account":
        return events

    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            phone_number_id = value.get("metadata", {}).get("phone_number_id", "")

            for msg in value.get("messages", []):
                if msg.get("type") != "interactive":
                    continue
                interactive = msg.get("interactive", {})
                itype = interactive.get("type", "")
                if itype != "nfm_context":
                    continue

                nfm = interactive.get("nfm_context", {})
                flow_token = nfm.get("flow_token", "")
                response_raw = nfm.get("response_json", "{}")

                try:
                    response_data = json.loads(response_raw) if isinstance(response_raw, str) else response_raw
                except (json.JSONDecodeError, TypeError):
                    response_data = {"raw": response_raw}

                timestamp = msg.get("timestamp", "0")
                try:
                    ts_int = int(timestamp)
                except (ValueError, TypeError):
                    ts_int = 0

                events.append({
                    "phone_number_id": phone_number_id,
                    "sender_phone": msg.get("from", ""),
                    "flow_token": flow_token,
                    "flow_id": nfm.get("flow_id", ""),
                    "response_json": response_data,
                    "message_id": msg.get("id", ""),
                    "timestamp": ts_int,
                })

    return events


# ------------------------------------------------------------------
# Flow Data Endpoint handler
# ------------------------------------------------------------------

def build_flow_endpoint_response(
    screen: str,
    data: dict | None = None,
    next_screen: str | None = None,
    error_message: str | None = None,
) -> dict:
    """Build a response for the Flow Data Endpoint.

    When a Flow uses a data_channel_uri, WhatsApp calls the endpoint with
    a POST request containing the current screen and form data. The endpoint
    responds with the next screen to display and any dynamic data.

    Args:
        screen: The next screen to navigate to.
        data: Data to merge into the screen's data model.
        next_screen: Override the routing target (optional).
        error_message: If set, display an error on the current screen.

    Returns: Response dict for the Flow endpoint.
    """
    response: dict[str, Any] = {
        "version": "3.0",
        "screen": screen,
    }
    if data:
        response["data"] = data
    if error_message:
        response["error"] = {"message": error_message}
    return response


def parse_flow_endpoint_request(body: dict) -> dict:
    """Parse an incoming Flow Data Endpoint request.

    WhatsApp sends a POST to the data_channel_uri with:
    - action: "ping" (health check) or "navigate" / "data_exchange"
    - screen: current screen name
    - data: form data from the current screen
    - flow_token: token for tracking
    - flow_id: Flow ID

    Returns a normalized dict with these fields.
    """
    action = body.get("action", "")
    if action == "ping":
        return {"action": "ping", "is_ping": True}

    return {
        "action": action,
        "is_ping": False,
        "screen": body.get("screen", ""),
        "data": body.get("data", {}),
        "flow_token": body.get("flow_token", ""),
        "flow_id": body.get("flow_id", ""),
        "flow_token_encrypted": body.get("flow_token_encrypted", ""),
        "encrypted_flow_data": body.get("encrypted_flow_data", ""),
    }
