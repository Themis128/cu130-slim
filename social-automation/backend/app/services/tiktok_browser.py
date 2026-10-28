"""TikTok browser automation service — drives the Playwright sidecar.

This service communicates with the ``tiktok-browser-sidecar`` Docker container
(a Node.js + Playwright REST API) to configure TikTok settings that the
official Display API does not expose.

Supported operations (no captcha required):
    - Privacy: private account toggle, comments permission, direct messages
    - Notifications: desktop notifications, interaction preferences
    - Ads: personalized ads toggle
    - Accessibility: color contrast toggle
    - Business verification: form fill (documents still need manual upload)
    - Profile read from browser (includes avatar URL)

Not supported (captcha-gated):
    - Bio/signature write
    - Avatar/profile picture upload
    - Nickname write
    - Username change
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from pydantic import BaseModel

from app.core.config import settings

logger = logging.getLogger(__name__)


class TikTokBrowserError(Exception):
    """Raised when the TikTok browser sidecar returns an error."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"TikTok browser error {status_code}: {detail}")


# ── Request models ───────────────────────────────────────────────────────────


class SessionRequest(BaseModel):
    session_id: str
    user_id: str | None = None


class ToggleRequest(BaseModel):
    enabled: bool


class CommentsRequest(BaseModel):
    permission: str  # "Everyone" or "Friends"


class DirectMessagesRequest(BaseModel):
    potential_connections: str | None = None  # "Friends" | "Followers" | "No one"
    others: str | None = None  # "Message request" | "Don't receive"


class InteractionNotificationsRequest(BaseModel):
    likes: bool | None = None
    comments: bool | None = None
    new_followers: bool | None = None
    mentions_and_tags: bool | None = None


class BusinessVerificationRequest(BaseModel):
    company_name: str | None = None
    website: str | None = None
    country: str | None = None
    address: str | None = None
    industry: str | None = None
    business_license_number: str | None = None


# ── Service ──────────────────────────────────────────────────────────────────


class TikTokBrowserService:
    """HTTP client for the TikTok browser automation sidecar."""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.TIKTOK_BROWSER_SIDECAR_URL).rstrip("/")
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=120.0)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            resp = await self.client.request(method, url, json=json)
        except httpx.ConnectError as e:
            raise TikTokBrowserError(503, f"TikTok browser sidecar unreachable: {e}")
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("error", resp.text[:300])
            except Exception:
                detail = resp.text[:300]
            raise TikTokBrowserError(resp.status_code, detail)
        return resp.json()

    # ── Health ───────────────────────────────────────────────────────────

    async def health(self) -> dict[str, Any]:
        return await self._request("GET", "/health")

    # ── Session ──────────────────────────────────────────────────────────

    async def set_session(self, session_id: str, user_id: str | None = None) -> dict[str, Any]:
        """Inject the TikTok session cookie into the browser and verify it."""
        return await self._request("POST", "/session", {
            "session_id": session_id,
            "user_id": user_id,
        })

    async def check_session(self) -> dict[str, Any]:
        """Check if the current browser session is still alive."""
        return await self._request("GET", "/session")

    # ── Profile read ─────────────────────────────────────────────────────

    async def read_profile(self) -> dict[str, Any]:
        """Read profile from the browser (includes avatar URL)."""
        return await self._request("GET", "/profile")

    # ── Privacy settings ─────────────────────────────────────────────────

    async def set_private_account(self, enabled: bool) -> dict[str, Any]:
        return await self._request("POST", "/privacy/private-account", {"enabled": enabled})

    async def set_comments(self, permission: str) -> dict[str, Any]:
        if permission not in ("Everyone", "Friends"):
            raise TikTokBrowserError(400, 'permission must be "Everyone" or "Friends"')
        return await self._request("POST", "/privacy/comments", {"permission": permission})

    async def set_direct_messages(
        self,
        potential_connections: str | None = None,
        others: str | None = None,
    ) -> dict[str, Any]:
        return await self._request("POST", "/privacy/direct-messages", {
            "potential_connections": potential_connections,
            "others": others,
        })

    # ── Notifications ────────────────────────────────────────────────────

    async def set_desktop_notifications(self, enabled: bool) -> dict[str, Any]:
        return await self._request("POST", "/notifications/desktop", {"enabled": enabled})

    async def set_interaction_notifications(
        self,
        likes: bool | None = None,
        comments: bool | None = None,
        new_followers: bool | None = None,
        mentions_and_tags: bool | None = None,
    ) -> dict[str, Any]:
        return await self._request("POST", "/notifications/interactions", {
            "likes": likes,
            "comments": comments,
            "new_followers": new_followers,
            "mentions_and_tags": mentions_and_tags,
        })

    # ── Ads ──────────────────────────────────────────────────────────────

    async def set_personalized_ads(self, enabled: bool) -> dict[str, Any]:
        return await self._request("POST", "/ads/personalized", {"enabled": enabled})

    # ── Accessibility ────────────────────────────────────────────────────

    async def set_color_contrast(self, enabled: bool) -> dict[str, Any]:
        return await self._request("POST", "/accessibility/contrast", {"enabled": enabled})

    # ── Business verification ────────────────────────────────────────────

    async def fill_business_verification(
        self,
        company_name: str | None = None,
        website: str | None = None,
        country: str | None = None,
        address: str | None = None,
        industry: str | None = None,
        business_license_number: str | None = None,
    ) -> dict[str, Any]:
        return await self._request("POST", "/business-verification/fill", {
            "company_name": company_name,
            "website": website,
            "country": country,
            "address": address,
            "industry": industry,
            "business_license_number": business_license_number,
        })

    async def get_business_verification_status(self) -> dict[str, Any]:
        return await self._request("GET", "/business-verification/status")

    # ── Read all settings ────────────────────────────────────────────────

    async def read_all_settings(self) -> dict[str, Any]:
        return await self._request("GET", "/settings")
