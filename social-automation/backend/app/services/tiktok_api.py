"""Standalone TikTok Content Posting API client.

This module wraps the TikTok Content Posting API calls needed for the Cloudless
social stack without depending on the database or Celery worker. It is consumed
by:

- ``app/api/tiktok.py`` for the TikTok endpoints
- ``app/services/publishing.py`` / analytics sync
- One-off scripts and n8n webhooks that need direct TikTok API access

TikTok uses an OAuth 2.0 Login Kit flow. Video posts support two source types:

- ``PULL_FROM_URL`` -- TikTok downloads the video from a public URL.
- ``FILE_UPLOAD`` -- the caller uploads binary data to a returned ``upload_url``.

Photo posts accept a list of public photo URLs. All requests require an
``Authorization: Bearer {token}`` header and ``Content-Type: application/json``.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

TIKTOK_API_BASE = "https://open.tiktokapis.com"
TIKTOK_API_VERSION = "v2"
MAX_TITLE_CHARS = 2200

logger = logging.getLogger(__name__)

# TikTok publish IDs and open IDs are alphanumeric with hyphens.
_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")


def _sanitize_log_text(text: str, max_len: int = 400) -> str:
    """Sanitize API response text for safe logging -- strips newlines/control chars."""
    cleaned = text.replace("\n", "\\n").replace("\r", "\\r")
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", cleaned)
    return cleaned[:max_len]


def _validate_id(value: str, field: str = "ID") -> str:
    """Validate a TikTok identifier to prevent injection via crafted values."""
    value = value.strip()
    if not value:
        raise ValueError(f"{field} is empty")
    if not _ID_RE.match(value):
        raise ValueError(f"Invalid {field} format: {value[:80]}")
    return value


class TikTokAPIError(Exception):
    """Raised when a TikTok API call fails with a non-success status.

    TikTok 5xx responses are surfaced as 502/503 gateway errors so the
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
            message = f"TikTok API error {status_code} for {url}: {response_text[:400]}"
        super().__init__(message)


class TikTokAPIClient:
    """Async TikTok Content Posting API client for a single access token.

    The client does not store decrypted tokens beyond the lifetime of the
    instance. Callers are responsible for encrypting tokens at rest.
    """

    def __init__(self, access_token: str, open_id: str):
        self.access_token = access_token
        self.open_id = _validate_id(open_id, "open_id")
        self._base_url = f"{TIKTOK_API_BASE}/{TIKTOK_API_VERSION}"

    def _headers(self) -> dict[str, str]:
        if not self.access_token:
            raise ValueError("TikTok access token is required")
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    def _map_status_code(self, status_code: int) -> int:
        """Normalize upstream 5xx status codes to 502/503 for the FastAPI layer."""
        if status_code >= 500:
            return 503 if status_code in (503, 504) else 502
        return status_code

    def _raise_for_status(self, resp: httpx.Response, url: str) -> None:
        """Raise ``TikTokAPIError`` for any non-2xx response."""
        if resp.status_code < 400:
            return
        status_code = self._map_status_code(resp.status_code)
        text = _sanitize_log_text(resp.text)
        safe_url = _sanitize_log_text(url)
        logger.error("TikTok API error %s for %s: %s", status_code, safe_url, text)
        raise TikTokAPIError(status_code, text, url)

    def _log_api_error(self, url: str, resp: httpx.Response) -> None:
        """Log the response body for a failed TikTok API call."""
        if resp.status_code >= 400:
            safe_url = _sanitize_log_text(url)
            logger.error(
                "TikTok API call to %s failed: HTTP %s: %s",
                safe_url,
                resp.status_code,
                _sanitize_log_text(resp.text),
            )

    def _check_tiktok_error(self, data: dict[str, Any], url: str) -> None:
        """Raise if the TikTok response envelope contains an error code."""
        error = data.get("error") or {}
        code = error.get("code")
        if code and code != "ok":
            message = error.get("message") or "Unknown TikTok API error"
            logger.error(
                "TikTok API business error %s for %s: %s",
                code,
                _sanitize_log_text(url),
                _sanitize_log_text(str(message)),
            )
            raise TikTokAPIError(
                400,
                _sanitize_log_text(str(message)),
                url,
                message=f"TikTok error {code}: {message}",
            )

    async def validate_token(self) -> dict[str, Any]:
        """Validate the access token and return user info.

        Raises ``TikTokAPIError`` on invalid, expired, or upstream error.
        """
        url = f"{self._base_url}/user/info/"
        fields = "open_id,union_id,avatar_url,display_name"
        params = {"fields": fields}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=self._headers(), params=params)
            self._raise_for_status(resp, url)
            data = resp.json() or {}
            self._check_tiktok_error(data, url)
            return data

    async def get_creator_info(self) -> dict[str, Any]:
        """Query creator info (privacy options, max video duration, etc.)."""
        url = f"{self._base_url}/post/publish/creator_info/query/"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=self._headers())
            self._raise_for_status(resp, url)
            data = resp.json() or {}
            self._check_tiktok_error(data, url)
            return data

    async def init_video_post(
        self,
        source: str = "PULL_FROM_URL",
        video_url: str = "",
        title: str = "",
        privacy_level: str = "PUBLIC",
    ) -> dict[str, Any]:
        """Initialize a video post and return the publish info.

        For ``PULL_FROM_URL`` the ``video_url`` must be a publicly accessible
        URL. For ``FILE_UPLOAD`` the response contains an ``upload_url`` that
        the caller must ``PUT`` the video bytes to.
        """
        if source not in ("PULL_FROM_URL", "FILE_UPLOAD"):
            raise ValueError("source must be 'PULL_FROM_URL' or 'FILE_UPLOAD'")
        if source == "PULL_FROM_URL" and not video_url:
            raise ValueError("video_url is required when source is PULL_FROM_URL")

        post_info: dict[str, Any] = {
            "title": title[:MAX_TITLE_CHARS],
            "privacy_level": privacy_level,
        }
        source_info: dict[str, Any] = {"source": source}
        if source == "PULL_FROM_URL":
            source_info["video_url"] = video_url

        payload = {
            "post_info": post_info,
            "source_info": source_info,
        }

        url = f"{self._base_url}/post/publish/video/init/"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=self._headers(), json=payload)
            self._raise_for_status(resp, url)
            data = resp.json() or {}
            self._check_tiktok_error(data, url)
            return data

    async def init_photo_post(
        self,
        photo_urls: list[str],
        title: str = "",
        privacy_level: str = "PUBLIC",
    ) -> dict[str, Any]:
        """Initialize a photo post and return the publish info.

        ``photo_urls`` must be a list of publicly accessible image URLs
        (maximum 35 photos per post).
        """
        if not photo_urls:
            raise ValueError("At least one photo URL is required")
        if len(photo_urls) > 35:
            raise ValueError("A photo post cannot have more than 35 photos")

        post_info: dict[str, Any] = {
            "title": title[:MAX_TITLE_CHARS],
            "privacy_level": privacy_level,
        }
        source_info: dict[str, Any] = {
            "source": "PULL_FROM_URL",
            "photo_images": photo_urls,
        }

        payload = {
            "post_info": post_info,
            "source_info": source_info,
        }

        url = f"{self._base_url}/post/publish/content/init/"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=self._headers(), json=payload)
            self._raise_for_status(resp, url)
            data = resp.json() or {}
            self._check_tiktok_error(data, url)
            return data

    async def check_publish_status(self, publish_id: str) -> dict[str, Any]:
        """Check the publish status of a previously initialized post."""
        publish_id = _validate_id(publish_id, "publish_id")
        url = f"{self._base_url}/post/publish/status/fetch/"
        payload = {"publish_id": publish_id}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=self._headers(), json=payload)
            self._raise_for_status(resp, url)
            data = resp.json() or {}
            self._check_tiktok_error(data, url)
            return data
