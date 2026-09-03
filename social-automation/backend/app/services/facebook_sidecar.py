"""HTTP client for the Facebook Browser Automation sidecar.

The sidecar is a Node.js + Playwright container (facebook-browser-sidecar)
that exposes a REST API on port 9226 for Facebook personal-profile operations
that the official Graph API does not support:

- Personal-profile posting (text, photo, link, video)
- Bio / intro text edits
- Profile picture and cover photo uploads
- Website in contact info
- Page-mode posting (list pages, switch, post as page)

This client wraps the sidecar's HTTP endpoints so the SocialAuto backend can
drive Facebook browser automation without running Playwright inside the
Python process.
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class FacebookSidecarError(Exception):
    """Raised when the Facebook sidecar returns an error."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Facebook sidecar error {status_code}: {detail}")


class FacebookSidecarClient:
    """HTTP client for the facebook-browser-sidecar service."""

    def __init__(self, base_url: str | None = None, timeout: float = 180.0) -> None:
        self.base_url = (
            base_url
            or os.environ.get("FACEBOOK_BROWSER_SIDECAR_URL")
            or "http://facebook-browser-sidecar:9226"
        )
        self._timeout = timeout

    @property
    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, timeout=self._timeout)

    async def _post(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        async with self._client as c:
            r = await c.post(path, json=json)
            if r.status_code >= 400:
                raise FacebookSidecarError(r.status_code, r.text)
            return r.json()

    async def _get(self, path: str) -> dict[str, Any]:
        async with self._client as c:
            r = await c.get(path)
            if r.status_code >= 400:
                raise FacebookSidecarError(r.status_code, r.text)
            return r.json()

    # ── Health & session ──────────────────────────────────────────────────

    async def health(self) -> dict[str, Any]:
        return await self._get("/health")

    async def set_session(self, storage_state: dict) -> dict[str, Any]:
        return await self._post("/session", {"storage_state": storage_state})

    async def check_session(self) -> dict[str, Any]:
        return await self._get("/session")

    # ── Personal profile ──────────────────────────────────────────────────

    async def get_profile(self) -> dict[str, Any]:
        return await self._get("/profile")

    async def update_bio(self, bio: str) -> dict[str, Any]:
        return await self._post("/profile/bio", {"bio": bio})

    async def upload_picture(self, image_bytes: bytes, filename: str = "profile.jpg") -> dict[str, Any]:
        b64 = base64.b64encode(image_bytes).decode()
        return await self._post("/profile/picture", {"image_base64": b64, "filename": filename})

    async def upload_cover(self, image_bytes: bytes, filename: str = "cover.jpg") -> dict[str, Any]:
        b64 = base64.b64encode(image_bytes).decode()
        return await self._post("/profile/cover", {"image_base64": b64, "filename": filename})

    async def update_website(self, website: str) -> dict[str, Any]:
        return await self._post("/profile/website", {"website": website})

    # ── Personal posting ──────────────────────────────────────────────────

    async def post_text(self, message: str, privacy: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"message": message}
        if privacy:
            payload["privacy"] = privacy
        return await self._post("/post/text", payload)

    async def post_photo(
        self,
        images: list[dict[str, str]],
        message: str | None = None,
        privacy: str | None = None,
    ) -> dict[str, Any]:
        """Post photo(s) to the personal profile.

        Args:
            images: list of dicts with ``image_base64`` and optional ``filename``.
            message: optional caption.
            privacy: 'public', 'friends', or 'only_me'.
        """
        payload: dict[str, Any] = {"images": images}
        if message:
            payload["message"] = message
        if privacy:
            payload["privacy"] = privacy
        return await self._post("/post/photo", payload)

    async def post_link(self, url: str, message: str | None = None, privacy: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"url": url}
        if message:
            payload["message"] = message
        if privacy:
            payload["privacy"] = privacy
        return await self._post("/post/link", payload)

    async def post_video(
        self,
        video_bytes: bytes,
        filename: str = "video.mp4",
        message: str | None = None,
        privacy: str | None = None,
    ) -> dict[str, Any]:
        b64 = base64.b64encode(video_bytes).decode()
        payload: dict[str, Any] = {"video_base64": b64, "filename": filename}
        if message:
            payload["message"] = message
        if privacy:
            payload["privacy"] = privacy
        return await self._post("/post/video", payload)

    # ── Page mode ─────────────────────────────────────────────────────────

    async def list_pages(self) -> dict[str, Any]:
        return await self._get("/pages")

    async def use_page(self, page_id: str) -> dict[str, Any]:
        return await self._post(f"/page/{page_id}/use", {})

    async def page_post_text(self, message: str) -> dict[str, Any]:
        return await self._post("/page/post/text", {"message": message})

    async def page_post_photo(
        self,
        images: list[dict[str, str]],
        message: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"images": images}
        if message:
            payload["message"] = message
        return await self._post("/page/post/photo", payload)
