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
MAX_PHOTO_TITLE_CHARS = 90
MAX_DESC_CHARS = 4000
MIN_CHUNK_SIZE = 5 * 1024 * 1024
DEFAULT_CHUNK_SIZE = 10 * 1024 * 1024
MAX_CHUNK_SIZE = 64 * 1024 * 1024
MAX_FINAL_CHUNK_SIZE = 128 * 1024 * 1024
MAX_CHUNK_COUNT = 1000
# Official Content Posting media restrictions:
# https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide
TIKTOK_MIN_FPS = 23
TIKTOK_MIN_DIMENSION = 360
TIKTOK_MAX_DIMENSION = 4096
TIKTOK_MAX_DURATION_SEC = 600

logger = logging.getLogger(__name__)


def _video_chunk_plan(video_size: int) -> tuple[int, int]:
    """Return ``(chunk_size, total_chunk_count)`` per TikTok media transfer rules.

    Rules (official Media Transfer Guide):
    - Videos under 5 MB must be uploaded whole (``chunk_size == video_size``).
    - Videos greater than 64 MB must be uploaded in multiple chunks.
    - For everything ≤ 64 MB we use a single whole-file upload with
      ``chunk_size == video_size`` so init metadata matches the PUT body.
    - Larger videos use 10 MB chunks; ``total_chunk_count`` is floor division
      and the final chunk may exceed ``chunk_size`` (up to 128 MB).
    """
    if video_size <= 0:
        raise ValueError("video_size must be positive")
    if video_size <= MAX_CHUNK_SIZE:
        return video_size, 1
    chunk_size = DEFAULT_CHUNK_SIZE
    total_chunk_count = _expected_chunk_count(video_size, chunk_size)
    final_chunk = video_size - (total_chunk_count - 1) * chunk_size
    if final_chunk > MAX_FINAL_CHUNK_SIZE:
        raise ValueError("final TikTok upload chunk would exceed 128 MB")
    if total_chunk_count > MAX_CHUNK_COUNT:
        raise ValueError("video requires more than 1000 chunks")
    return chunk_size, total_chunk_count


def _expected_chunk_count(video_size: int, chunk_size: int) -> int:
    """Floor division matching TikTok's documented total_chunk_count rule."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    return max(1, video_size // chunk_size)

# TikTok publish IDs and open IDs are alphanumeric with hyphens.
# FILE_UPLOAD publish IDs use a format like "v_inbox_file~v2.7681096048080848918"
# which includes ~ and . characters.
_ID_RE = re.compile(r"^[a-zA-Z0-9_\-~.]+$")


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
        # Official Content Posting / Display API docs require charset=UTF-8.
        # https://developers.tiktok.com/doc/content-posting-api-reference-query-creator-info
        if not self.access_token:
            raise ValueError("TikTok access token is required")
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json; charset=UTF-8",
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
        privacy_level: str = "SELF_ONLY",
        video_size: int | None = None,
        chunk_size: int | None = None,
        total_chunk_count: int = 1,
        dry_run: bool = False,
        *,
        disable_duet: bool | None = None,
        disable_stitch: bool | None = None,
        disable_comment: bool | None = None,
        video_cover_timestamp_ms: int | None = None,
        brand_content_toggle: bool = False,
        brand_organic_toggle: bool = False,
        is_aigc: bool | None = None,
    ) -> dict[str, Any]:
        """Initialize a video post and return the publish info.

        For ``PULL_FROM_URL`` the ``video_url`` must be a publicly accessible
        URL. For ``FILE_UPLOAD`` the response contains an ``upload_url`` that
        the caller must ``PUT`` the video bytes to.

        When ``dry_run`` is True, TikTok validates the request (title,
        privacy level, source) without consuming a pending-share slot or
        uploading any media.  Useful for pre-flight checks before the real
        publish attempt, especially when the account is near TikTok's
        5-per-24h pending-share spam limit.

        Direct Post ``post_info`` fields follow:
        https://developers.tiktok.com/doc/content-posting-api-reference-direct-post
        """
        if source not in ("PULL_FROM_URL", "FILE_UPLOAD"):
            raise ValueError("source must be 'PULL_FROM_URL' or 'FILE_UPLOAD'")
        if source == "PULL_FROM_URL" and not video_url:
            raise ValueError("video_url is required when source is PULL_FROM_URL")

        post_info: dict[str, Any] = {
            "title": title[:MAX_TITLE_CHARS],
            "privacy_level": privacy_level,
            # Required by TikTok Direct Post schema (paid partnership flags).
            "brand_content_toggle": bool(brand_content_toggle),
            "brand_organic_toggle": bool(brand_organic_toggle),
        }
        if disable_duet is not None:
            post_info["disable_duet"] = bool(disable_duet)
        if disable_stitch is not None:
            post_info["disable_stitch"] = bool(disable_stitch)
        if disable_comment is not None:
            post_info["disable_comment"] = bool(disable_comment)
        if video_cover_timestamp_ms is not None and video_cover_timestamp_ms >= 0:
            post_info["video_cover_timestamp_ms"] = int(video_cover_timestamp_ms)
        if is_aigc is not None:
            post_info["is_aigc"] = bool(is_aigc)

        source_info: dict[str, Any] = {"source": source}
        if source == "PULL_FROM_URL":
            source_info["video_url"] = video_url
        else:
            if not video_size or video_size <= 0:
                raise ValueError("video_size must be positive when source is FILE_UPLOAD")
            planned_chunk_size, planned_chunk_count = _video_chunk_plan(video_size)
            resolved_chunk_size = chunk_size or planned_chunk_size
            resolved_chunk_count = (
                total_chunk_count if chunk_size is not None else planned_chunk_count
            )
            if resolved_chunk_size <= 0 or resolved_chunk_size > min(video_size, MAX_CHUNK_SIZE):
                raise ValueError("chunk_size must be positive and no larger than 64 MB")
            if resolved_chunk_count <= 0 or resolved_chunk_count > MAX_CHUNK_COUNT:
                raise ValueError("total_chunk_count must be between 1 and 1000")
            if resolved_chunk_count != _expected_chunk_count(video_size, resolved_chunk_size):
                raise ValueError("total_chunk_count does not match video_size and chunk_size")
            source_info.update(
                {
                    "video_size": video_size,
                    "chunk_size": resolved_chunk_size,
                    "total_chunk_count": resolved_chunk_count,
                }
            )

        payload: dict[str, Any] = {
            "post_info": post_info,
            "source_info": source_info,
        }
        if dry_run:
            payload["post_info"]["dry_run"] = True

        url = f"{self._base_url}/post/publish/video/init/"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=self._headers(), json=payload)
            self._raise_for_status(resp, url)
            data = resp.json() or {}
            self._check_tiktok_error(data, url)
            return data

    async def init_video_upload(
        self,
        source: str = "PULL_FROM_URL",
        video_url: str = "",
        video_size: int | None = None,
        chunk_size: int | None = None,
        total_chunk_count: int = 1,
    ) -> dict[str, Any]:
        if source not in ("PULL_FROM_URL", "FILE_UPLOAD"):
            raise ValueError("source must be 'PULL_FROM_URL' or 'FILE_UPLOAD'")

        source_info: dict[str, Any] = {"source": source}
        if source == "PULL_FROM_URL":
            if not video_url:
                raise ValueError("video_url is required when source is PULL_FROM_URL")
            source_info["video_url"] = video_url
        else:
            if not video_size or video_size <= 0:
                raise ValueError("video_size must be positive when source is FILE_UPLOAD")
            planned_chunk_size, planned_chunk_count = _video_chunk_plan(video_size)
            resolved_chunk_size = chunk_size or planned_chunk_size
            resolved_chunk_count = (
                total_chunk_count if chunk_size is not None else planned_chunk_count
            )
            if resolved_chunk_size <= 0 or resolved_chunk_size > min(video_size, MAX_CHUNK_SIZE):
                raise ValueError("chunk_size must be positive and no larger than 64 MB")
            if resolved_chunk_count <= 0 or resolved_chunk_count > MAX_CHUNK_COUNT:
                raise ValueError("total_chunk_count must be between 1 and 1000")
            if resolved_chunk_count != _expected_chunk_count(video_size, resolved_chunk_size):
                raise ValueError(
                    "total_chunk_count must equal video_size divided by chunk_size, rounded down"
                )
            source_info.update(
                {
                    "video_size": video_size,
                    "chunk_size": resolved_chunk_size,
                    "total_chunk_count": resolved_chunk_count,
                }
            )

        url = f"{self._base_url}/post/publish/inbox/video/init/"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=self._headers(), json={"source_info": source_info})
            self._raise_for_status(resp, url)
            data = resp.json() or {}
            self._check_tiktok_error(data, url)
            return data

    async def upload_video_file(
        self,
        upload_url: str,
        video_bytes: bytes,
        content_type: str = "video/mp4",
        chunk_size: int | None = None,
    ) -> None:
        from urllib.parse import urlparse

        parsed = urlparse(upload_url)
        # TikTok returns regional upload hosts (e.g. upload.us.tiktokapis.com,
        # open-upload.tiktokapis.com). Accept any *.tiktokapis.com host.
        if parsed.scheme != "https" or not (parsed.hostname or "").endswith(".tiktokapis.com"):
            raise ValueError("upload_url must use a *.tiktokapis.com host")
        if not video_bytes:
            raise ValueError("video_bytes cannot be empty")
        if content_type not in ("video/mp4", "video/quicktime", "video/webm"):
            raise ValueError("unsupported TikTok video content type")

        size = len(video_bytes)
        resolved_chunk_size, total_chunk_count = _video_chunk_plan(size)
        if chunk_size is not None:
            # Whole-file uploads declare chunk_size == size (including <5 MB).
            # Multi-chunk uploads require each non-final chunk in [5 MB, 64 MB].
            if chunk_size == size:
                resolved_chunk_size, total_chunk_count = size, 1
            else:
                if chunk_size < MIN_CHUNK_SIZE or chunk_size > min(size, MAX_CHUNK_SIZE):
                    raise ValueError("chunk_size is outside TikTok's allowed range")
                resolved_chunk_size = chunk_size
                total_chunk_count = _expected_chunk_count(size, resolved_chunk_size)
                if total_chunk_count > MAX_CHUNK_COUNT:
                    raise ValueError("video requires more than 1000 chunks")
        offset = 0
        async with httpx.AsyncClient(timeout=300.0) as client:
            for index in range(total_chunk_count):
                is_last = index == total_chunk_count - 1
                end = size if is_last else offset + resolved_chunk_size
                chunk = video_bytes[offset:end]
                if is_last and len(chunk) > MAX_FINAL_CHUNK_SIZE:
                    raise ValueError("final TikTok upload chunk exceeds 128 MB")
                headers = {
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {offset}-{end - 1}/{size}",
                    "Content-Type": content_type,
                }
                resp = await client.put(upload_url, headers=headers, content=chunk)
                self._raise_for_status(resp, upload_url)
                # Official media transfer guide: intermediate chunks → 206,
                # final chunk → 201 Created.
                if is_last and resp.status_code not in (200, 201):
                    logger.warning(
                        "TikTok final chunk upload returned HTTP %s (expected 201)",
                        resp.status_code,
                    )
                elif not is_last and resp.status_code not in (200, 206):
                    logger.warning(
                        "TikTok intermediate chunk upload returned HTTP %s (expected 206)",
                        resp.status_code,
                    )
                offset = end

    async def init_photo_post(
        self,
        photo_urls: list[str],
        title: str = "",
        privacy_level: str = "SELF_ONLY",
        *,
        description: str = "",
        disable_comment: bool | None = None,
        auto_add_music: bool = False,
        brand_content_toggle: bool = False,
        brand_organic_toggle: bool = False,
        is_aigc: bool | None = None,
        photo_cover_index: int = 0,
    ) -> dict[str, Any]:
        """Initialize a photo post and return the publish info.

        ``photo_urls`` must be a list of publicly accessible image URLs
        (maximum 35 photos per post).

        ``auto_add_music`` lets TikTok attach recommended music (no specific
        track selection via API — official Content Posting limitation).
        """
        if not photo_urls:
            raise ValueError("At least one photo URL is required")
        if len(photo_urls) > 35:
            raise ValueError("A photo post cannot have more than 35 photos")
        cover = max(0, min(int(photo_cover_index), len(photo_urls) - 1))

        post_info: dict[str, Any] = {
            "title": title[:MAX_PHOTO_TITLE_CHARS],
            "privacy_level": privacy_level,
            "brand_content_toggle": bool(brand_content_toggle),
            "brand_organic_toggle": bool(brand_organic_toggle),
            "auto_add_music": bool(auto_add_music),
        }
        if description:
            post_info["description"] = description[:MAX_DESC_CHARS]
        if disable_comment is not None:
            post_info["disable_comment"] = bool(disable_comment)
        if is_aigc is not None:
            post_info["is_aigc"] = bool(is_aigc)

        source_info: dict[str, Any] = {
            "source": "PULL_FROM_URL",
            "photo_cover_index": cover,
            "photo_images": photo_urls,
        }

        payload = {
            "post_info": post_info,
            "source_info": source_info,
            "post_mode": "DIRECT_POST",
            "media_type": "PHOTO",
        }

        url = f"{self._base_url}/post/publish/content/init/"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=self._headers(), json=payload)
            self._raise_for_status(resp, url)
            data = resp.json() or {}
            self._check_tiktok_error(data, url)
            return data

    async def init_photo_post_media_upload(
        self,
        photo_urls: list[str],
        title: str = "",
        description: str = "",
    ) -> dict[str, Any]:
        """Initialize a photo carousel via inbox PULL_FROM_URL.

        This is the free-tier friendly flow that sends the post to the
        creator's TikTok inbox for manual review before publishing.
        ``photo_urls`` must be publicly accessible (max 35).
        """
        if not photo_urls:
            raise ValueError("At least one photo URL is required")
        if len(photo_urls) > 35:
            raise ValueError("A photo post cannot have more than 35 photos")

        payload = {
            "post_info": {
                "title": title[:MAX_PHOTO_TITLE_CHARS],
                "description": description[:MAX_DESC_CHARS],
            },
            "source_info": {
                "source": "PULL_FROM_URL",
                # Official docs: index starting from 0.
                "photo_cover_index": 0,
                "photo_images": photo_urls,
            },
            "post_mode": "MEDIA_UPLOAD",
            "media_type": "PHOTO",
        }

        url = f"{self._base_url}/post/publish/content/init/"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=self._headers(), json=payload)
            self._raise_for_status(resp, url)
            data = resp.json() or {}
            self._check_tiktok_error(data, url)
            return data

    async def check_publish_status(self, publish_id: str) -> dict[str, Any]:
        """Poll post status via official Get Post Status endpoint.

        POST /v2/post/publish/status/fetch/
        Scope: video.upload or video.publish
        Docs: https://developers.tiktok.com/doc/content-posting-api-reference-get-video-status
        Rate limit: 30 requests/min per user access token.
        """
        publish_id = _validate_id(publish_id, "publish_id")
        url = f"{self._base_url}/post/publish/status/fetch/"
        payload = {"publish_id": publish_id}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=self._headers(), json=payload)
            self._raise_for_status(resp, url)
            data = resp.json() or {}
            self._check_tiktok_error(data, url)
            return data

    async def cancel_publish(self, publish_id: str) -> dict[str, Any]:
        """Cancel an ongoing PULL_FROM_URL / upload task (best-effort).

        POST /v2/post/publish/cancel/
        Scope: video.upload or video.publish
        Docs: https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide
        Final states return ``publish_not_cancellable``.
        """
        publish_id = _validate_id(publish_id, "publish_id")
        url = f"{self._base_url}/post/publish/cancel/"
        payload = {"publish_id": publish_id}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=self._headers(), json=payload)
            self._raise_for_status(resp, url)
            data = resp.json() or {}
            self._check_tiktok_error(data, url)
            return data

    # ── Display API: video listing & per-video analytics ──────────────────

    async def list_videos(
        self,
        fields: str = (
            "id,create_time,cover_image_url,share_url,video_description,"
            "duration,title,like_count,comment_count,share_count,view_count"
        ),
        cursor: int = 0,
        max_count: int = 20,
    ) -> dict[str, Any]:
        """List the creator's public videos via Display API.

        POST /v2/video/list/?fields=...
        Scope: video.list
        Docs: https://developers.tiktok.com/doc/tiktok-api-v2-video-list
        ``max_count`` default 10, maximum 20 (official).
        ``cursor`` is a UTC Unix timestamp in milliseconds when paginating.
        """
        if max_count < 1 or max_count > 20:
            raise ValueError("max_count must be between 1 and 20 (TikTok Display API limit)")
        url = f"{self._base_url}/video/list/"
        payload: dict[str, Any] = {"max_count": max_count}
        if cursor:
            payload["cursor"] = cursor
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                headers=self._headers(),
                params={"fields": fields},
                json=payload,
            )
            self._raise_for_status(resp, url)
            data = resp.json() or {}
            self._check_tiktok_error(data, url)
            return data

    async def query_video(
        self,
        video_ids: list[str],
        fields: str = (
            "id,create_time,cover_image_url,share_url,video_description,"
            "duration,title,like_count,comment_count,share_count,view_count"
        ),
    ) -> dict[str, Any]:
        """Query specific videos by ID via Display API.

        POST /v2/video/query/?fields=...
        Scope: video.list
        Docs: https://developers.tiktok.com/doc/tiktok-api-v2-video-query
        Up to 20 video IDs per request (official).
        """
        if not video_ids:
            raise ValueError("At least one video_id is required")
        if len(video_ids) > 20:
            raise ValueError("Cannot query more than 20 videos at once (TikTok Display API limit)")
        url = f"{self._base_url}/video/query/"
        payload = {"filters": {"video_ids": video_ids}}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                headers=self._headers(),
                params={"fields": fields},
                json=payload,
            )
            self._raise_for_status(resp, url)
            data = resp.json() or {}
            self._check_tiktok_error(data, url)
            return data

    # ── Business Messaging API (DM) ───────────────────────────────────────
    # TikTok Business Messaging API is in Open Beta (APAC, LATAM, METAP,
    # North America). Not yet available in EU/Greece.
    # Only for inbound messages (users must message first).
    # 48-hour reply window, 10 messages per window, 100-200 conv/day.
    # Requires a TikTok Business Account and approved messaging partner.
    #
    # These methods are ready for when the API becomes available in EU or
    # the account upgrades to Business. They follow the official docs at
    # https://business-api.tiktok.com/portal/docs/business-messaging-api

    async def list_dm_conversations(
        self,
        cursor: str = "",
        page_size: int = 20,
    ) -> dict[str, Any]:
        """List TikTok Business DM conversations.

        Returns conversations where users have messaged the business account.
        Requires Business Messaging API access.
        """
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")

        url = f"{self._base_url}/business/messages/conversations"
        params = {"open_id": self.open_id, "page_size": page_size}
        if cursor:
            params["cursor"] = cursor
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=self._headers(), params=params)
            self._raise_for_status(resp, url)
            data = resp.json() or {}
            self._check_tiktok_error(data, url)
            return data

    async def get_dm_messages(
        self,
        conversation_id: str,
        cursor: str = "",
        page_size: int = 20,
    ) -> dict[str, Any]:
        """Get messages in a TikTok Business DM conversation.

        Returns messages in a specific conversation.
        Requires Business Messaging API access.
        """
        if not conversation_id:
            raise ValueError("conversation_id is required")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")

        url = f"{self._base_url}/business/messages/conversations/{conversation_id}/messages"
        params = {"open_id": self.open_id, "page_size": page_size}
        if cursor:
            params["cursor"] = cursor
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=self._headers(), params=params)
            self._raise_for_status(resp, url)
            data = resp.json() or {}
            self._check_tiktok_error(data, url)
            return data

    async def send_dm(
        self,
        conversation_id: str,
        content: dict[str, Any],
        content_type: str = "text",
    ) -> dict[str, Any]:
        """Send a message in a TikTok Business DM conversation.

        Only reply to users who have messaged first (inbound only).
        48-hour reply window, 10 messages per window.

        Args:
            conversation_id: The conversation ID.
            content: Message content dict (e.g., {"text": "Hello!"}).
            content_type: Message type ("text", "image").
        """
        if not conversation_id:
            raise ValueError("conversation_id is required")
        if not content:
            raise ValueError("content is required")

        url = f"{self._base_url}/business/messages/conversations/{conversation_id}/send"
        payload = {
            "open_id": self.open_id,
            "content_type": content_type,
            "content": content,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=self._headers(), json=payload)
            self._raise_for_status(resp, url)
            data = resp.json() or {}
            self._check_tiktok_error(data, url)
            return data
