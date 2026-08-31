"""Standalone Threads REST API client.

This module wraps the Threads API calls needed for the Cloudless social stack
without depending on the database or Celery worker. It is consumed by:

- ``app/api/threads.py`` for the Threads endpoints
- ``app/services/publishing.py`` / analytics sync
- One-off scripts and n8n webhooks that need direct Threads API access

Threads uses a two-step publishing flow:

1. Create a media container (``POST /{user_id}/threads``) which returns a
   ``creation_id``.
2. Publish the container (``POST /{user_id}/threads_publish``) which returns the
   final ``media_id``.

Media (images/videos) must be hosted at a public URL. The rate limit is 250
posts per 24 hours per account.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

THREADS_API_BASE = "https://graph.threads.net"
MAX_TEXT_CHARS = 500

logger = logging.getLogger(__name__)

# Threads media IDs are numeric strings.
_MEDIA_ID_RE = re.compile(r"^\d+$")


def _sanitize_log_text(text: str, max_len: int = 400) -> str:
    """Sanitize API response text for safe logging -- strips newlines/control chars."""
    cleaned = text.replace("\n", "\\n").replace("\r", "\\r")
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", cleaned)
    return cleaned[:max_len]


def _validate_media_id(media_id: str) -> str:
    """Validate a Threads media/creation ID to prevent injection via crafted identifiers."""
    media_id = media_id.strip()
    if not media_id:
        raise ValueError("Media ID is empty")
    if not _MEDIA_ID_RE.match(media_id):
        raise ValueError(f"Invalid Threads media ID format: {media_id[:80]}")
    return media_id


class ThreadsAPIError(Exception):
    """Raised when a Threads API call fails with a non-success status.

    Threads 5xx responses are surfaced as 502/503 gateway errors so the
    FastAPI layer can propagate them as ``HTTPException`` without leaking
    raw upstream status codes.
    """

    def __init__(
        self,
        status_code: int,
        response_text: str,
        url: str,
        message: str | None = None,
    ):
        self.status_code = status_code
        self.response_text = response_text
        self.url = url
        if message is None:
            message = f"Threads API error {status_code} for {url}: {response_text[:400]}"
        super().__init__(message)


class ThreadsAPIClient:
    """Async Threads API client for a single access token and user.

    The client does not store decrypted tokens beyond the lifetime of the
    instance. Callers are responsible for encrypting tokens at rest.
    """

    def __init__(
        self,
        access_token: str,
        user_id: str,
        api_version: str = "v1.0",
    ):
        self.access_token = access_token
        self.user_id = str(user_id)
        self.api_version = api_version
        self._base_url = f"{THREADS_API_BASE}/{api_version}"

    def _headers(self) -> dict[str, str]:
        if not self.access_token:
            raise ValueError("Threads access token is required")
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _params(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"access_token": self.access_token}
        if extra:
            params.update(extra)
        return params

    def _map_status_code(self, status_code: int) -> int:
        """Normalize upstream 5xx status codes to 502/503 for the FastAPI layer."""
        if status_code >= 500:
            return 503 if status_code in (503, 504) else 502
        return status_code

    def _raise_for_status(self, resp: httpx.Response, url: str) -> None:
        """Raise ``ThreadsAPIError`` for any non-2xx response."""
        if resp.status_code < 400:
            return
        status_code = self._map_status_code(resp.status_code)
        text = _sanitize_log_text(resp.text)
        safe_url = _sanitize_log_text(url)
        logger.error("Threads API error %s for %s: %s", status_code, safe_url, text)
        raise ThreadsAPIError(status_code, text, url)

    def _log_api_error(self, url: str, resp: httpx.Response) -> None:
        """Log the response body for a failed Threads API call."""
        if resp.status_code >= 400:
            safe_url = _sanitize_log_text(url)
            logger.error(
                "Threads API call to %s failed: HTTP %s: %s",
                safe_url,
                resp.status_code,
                _sanitize_log_text(resp.text),
            )

    async def validate_token(self) -> dict[str, Any]:
        """Validate the access token and return basic profile info.

        Raises ``ThreadsAPIError`` on invalid, expired, or upstream error.
        """
        url = f"{self._base_url}/me"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=self._headers(), params=self._params())
            self._raise_for_status(resp, url)
            return resp.json()

    async def _create_container(
        self,
        media_type: str,
        *,
        text: str = "",
        image_url: str = "",
        video_url: str = "",
        is_carousel_item: bool = False,
        children: list[str] | None = None,
    ) -> str:
        """Create a Threads media container and return the ``creation_id``."""
        payload: dict[str, Any] = {
            "media_type": media_type,
            "access_token": self.access_token,
        }
        if text:
            payload["text"] = text[:MAX_TEXT_CHARS]
        if image_url:
            payload["image_url"] = image_url
        if video_url:
            payload["video_url"] = video_url
        if is_carousel_item:
            payload["is_carousel_item"] = "true"
        if children:
            payload["children"] = ",".join(children)

        url = f"{self._base_url}/{self.user_id}/threads"
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, data=payload)
            self._raise_for_status(resp, url)
            data = resp.json() or {}
            creation_id = data.get("id")
            if not creation_id:
                raise ThreadsAPIError(
                    resp.status_code,
                    _sanitize_log_text(resp.text),
                    url,
                    message="Threads container creation returned no creation_id",
                )
            return str(creation_id)

    async def create_text_container(self, text: str) -> str:
        """Create a text-only container and return the ``creation_id``."""
        if not text or not text.strip():
            raise ValueError("Text content is required for a text post")
        return await self._create_container("TEXT", text=text)

    async def create_image_container(self, image_url: str, text: str = "") -> str:
        """Create an image container and return the ``creation_id``.

        ``image_url`` must be a publicly accessible URL.
        """
        if not image_url:
            raise ValueError("Image URL is required for an image post")
        return await self._create_container("IMAGE", text=text, image_url=image_url)

    async def create_video_container(self, video_url: str, text: str = "") -> str:
        """Create a video container and return the ``creation_id``.

        ``video_url`` must be a publicly accessible URL.
        """
        if not video_url:
            raise ValueError("Video URL is required for a video post")
        return await self._create_container("VIDEO", text=text, video_url=video_url)

    async def create_carousel_item(self, image_url: str, is_carousel_item: bool = True) -> str:
        """Create a single carousel item container and return its ``creation_id``.

        Carousel items are created individually then combined via
        ``create_carousel_container``.
        """
        if not image_url:
            raise ValueError("Image URL is required for a carousel item")
        return await self._create_container(
            "IMAGE",
            image_url=image_url,
            is_carousel_item=is_carousel_item,
        )

    async def create_carousel_container(self, children_ids: list[str], text: str = "") -> str:
        """Create a carousel container from previously created item IDs.

        ``children_ids`` must be a list of ``creation_id`` values returned by
        ``create_carousel_item``.
        """
        if not children_ids or len(children_ids) < 2:
            raise ValueError("At least two carousel item IDs are required")
        if len(children_ids) > 20:
            raise ValueError("A carousel cannot have more than 20 items")
        return await self._create_container(
            "CAROUSEL",
            text=text,
            children=children_ids,
        )

    async def publish_container(self, creation_id: str) -> str:
        """Publish a previously created container and return the final ``media_id``."""
        creation_id = _validate_media_id(creation_id)
        url = f"{self._base_url}/{self.user_id}/threads_publish"
        payload: dict[str, Any] = {
            "creation_id": creation_id,
            "access_token": self.access_token,
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, data=payload)
            self._raise_for_status(resp, url)
            data = resp.json() or {}
            media_id = data.get("id")
            if not media_id:
                raise ThreadsAPIError(
                    resp.status_code,
                    _sanitize_log_text(resp.text),
                    url,
                    message="Threads publish returned no media_id",
                )
            return str(media_id)

    async def get_insights(self, metric: str = "views") -> dict[str, Any]:
        """Fetch account-level insights for the authenticated user."""
        url = f"{self._base_url}/{self.user_id}/insights"
        params = self._params({"metric": metric})
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=self._headers(), params=params)
            self._raise_for_status(resp, url)
            return resp.json()

    async def delete_post(self, media_id: str) -> bool:
        """Delete a published Threads post by media ID.

        Returns ``True`` on success, ``False`` if the post could not be deleted.
        """
        media_id = _validate_media_id(media_id)
        url = f"{self._base_url}/{media_id}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.delete(url, headers=self._headers(), params=self._params())
            if resp.status_code >= 400:
                self._log_api_error(url, resp)
                return False
            return True
