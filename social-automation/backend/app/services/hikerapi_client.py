"""HikerAPI read-only fallback client for Instagram data.

Used when the local instagrapi private API is throttled (429,
PleaseWaitFewMinutes) or proxy-blocked.  HikerAPI is a paid SaaS that
provides public Instagram data through a REST API without requiring
Instagram authentication or app review.

API docs: https://hikerapi.com/instagram-api
ReDoc:    https://api.hikerapi.com/redoc

Requires ``HIKER_API_KEY`` in settings.  100 free requests on signup.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.hikerapi.com"
_TIMEOUT = 15.0


class HikerAPIError(Exception):
    """Raised when a HikerAPI request fails."""


class HikerAPIClient:
    """Async read-only client for Instagram profile and media data."""

    def __init__(self, api_key: str | None = None) -> None:
        self._settings = get_settings()
        self._api_key = api_key or self._settings.HIKER_API_KEY
        self._enabled = bool(self._api_key)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _headers(self) -> dict[str, str]:
        return {"x-access-key": self._api_key, "Accept": "application/json"}

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._enabled:
            raise HikerAPIError("HikerAPI is not configured (HIKER_API_KEY is empty)")
        url = f"{_BASE_URL}{path}"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(url, params=params, headers=self._headers())
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as exc:
            raise HikerAPIError(f"HikerAPI HTTP {exc.response.status_code}: {exc.response.text[:200]}") from exc
        except httpx.HTTPError as exc:
            raise HikerAPIError(f"HikerAPI request failed: {exc}") from exc

    # ── User profile ──────────────────────────────────────────────────────

    async def get_user_by_username(self, username: str) -> dict[str, Any]:
        """Get a user object by username. Returns pk, full_name, biography,
        follower_count, media_count, is_private, is_verified, profile_pic_url."""
        return await self._get("/v1/user/by/username", {"username": username})

    async def get_user_by_id(self, user_id: str | int) -> dict[str, Any]:
        """Get a user object by numeric ID."""
        return await self._get("/v1/user/by/id", {"id": str(user_id)})

    async def get_user_medias(self, username: str, amount: int = 12) -> list[dict[str, Any]]:
        """Get recent media posts for a user."""
        result = await self._get("/v1/user/medias", {"username": username, "amount": amount})
        if isinstance(result, list):
            return result
        return result.get("medias", result.get("items", []))

    async def get_user_about(self, username: str) -> dict[str, Any]:
        """Get the 'About this account' panel for a user."""
        return await self._get("/v1/user/about", {"username": username})

    # ── Media ─────────────────────────────────────────────────────────────

    async def get_media_info(self, media_id: str) -> dict[str, Any]:
        """Get information about a specific media post."""
        return await self._get("/v1/media/by/id", {"id": str(media_id)})

    async def get_media_comments(self, media_id: str, amount: int = 20) -> list[dict[str, Any]]:
        """Get comments for a media post."""
        result = await self._get("/v1/media/comments", {"id": str(media_id), "amount": amount})
        if isinstance(result, list):
            return result
        return result.get("comments", result.get("items", []))

    # ── Stories ────────────────────────────────────────────────────────────

    async def get_user_stories(self, username: str) -> list[dict[str, Any]]:
        """Get active stories for a user. Costs 2 requests per call."""
        result = await self._get("/v1/user/stories", {"username": username})
        if isinstance(result, list):
            return result
        return result.get("stories", result.get("items", []))


# Singleton
hiker_client = HikerAPIClient()
