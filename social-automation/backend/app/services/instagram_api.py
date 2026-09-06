"""Standalone Instagram Graph API client.

This module wraps the Instagram Graph API calls needed for the Cloudless
social stack without depending on the database or Celery worker. It is
consumed by:

- ``app/api/instagram.py`` for the Instagram endpoints
- ``app/services/publishing.py`` / analytics sync
- One-off scripts and n8n webhooks that need direct Instagram API access

Instagram publishing requires a Business or Creator account linked to a
Facebook Page. The two-step flow is:

1. Create a media container (image, video, or carousel).
2. Publish the container via ``/{ig-user-id}/media_publish``.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx

INSTAGRAM_DEFAULT_API_VERSION = "v25.0"
INSTAGRAM_API_BASE = "https://graph.facebook.com"
INSTAGRAM_BUSINESS_LOGIN_API_BASE = "https://graph.instagram.com"
MAX_CAPTION_CHARS = 2200

logger = logging.getLogger(__name__)

# Instagram media/container ids are numeric strings.
_ID_RE = re.compile(r"^\d+$")


def _validate_id(value: str, label: str = "id") -> str:
    """Validate that an Instagram id is a non-empty numeric string."""
    value = value.strip()
    if not value:
        raise ValueError(f"Instagram {label} is empty")
    if not _ID_RE.match(value):
        raise ValueError(f"Invalid Instagram {label} format: {value[:80]}")
    return value


def _sanitize_log_text(text: str, max_len: int = 400) -> str:
    """Sanitize API response text for safe logging - strips newlines/control chars."""
    cleaned = text.replace("\n", "\\n").replace("\r", "\\r")
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", cleaned)
    return cleaned[:max_len]


class InstagramAPIError(Exception):
    """Raised when an Instagram Graph API call fails with a non-success status.

    Upstream 5xx responses are surfaced as 502/503 gateway errors so the
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
            message = f"Instagram API error {status_code} for {url}: {response_text[:400]}"
        super().__init__(message)

    @property
    def safe_detail(self) -> str:
        """Return a client-safe error message without upstream URL/response details."""
        if 400 <= self.status_code < 500:
            return f"Instagram API request failed ({self.status_code})"
        return f"Instagram API temporarily unavailable ({self.status_code})"


class InstagramAPIClient:
    """Async Instagram Graph API client for a single access token.

    The client does not store decrypted tokens beyond the lifetime of the
    instance. Callers are responsible for encrypting tokens at rest.
    """

    def __init__(
        self,
        access_token: str,
        ig_user_id: str,
        api_version: str = INSTAGRAM_DEFAULT_API_VERSION,
        use_business_login_api: bool = False,
    ):
        if not access_token:
            raise ValueError("Instagram access token is required")
        self.access_token = access_token
        self.ig_user_id = _validate_id(ig_user_id, "ig_user_id")
        self.api_version = api_version
        api_base = INSTAGRAM_BUSINESS_LOGIN_API_BASE if use_business_login_api else INSTAGRAM_API_BASE
        self.base_url = f"{api_base}/{api_version}"

    def _params(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return base query params including the access token."""
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
        """Raise ``InstagramAPIError`` for any non-2xx response."""
        if resp.status_code < 400:
            return
        status_code = self._map_status_code(resp.status_code)
        text = _sanitize_log_text(resp.text)
        safe_url = _sanitize_log_text(url)
        logger.error("Instagram API error %s for %s: %s", status_code, safe_url, text)
        raise InstagramAPIError(status_code, text, url)

    async def validate_token(self) -> dict[str, Any]:
        """Validate the access token by fetching the linked Facebook Page info.

        Raises ``InstagramAPIError`` on invalid, expired, or upstream error.
        """
        url = f"{self.base_url}/me"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, params=self._params())
            self._raise_for_status(resp, url)
            return resp.json()

    async def get_profile(self) -> dict[str, Any]:
        """Fetch the Instagram Business/Creator account profile."""
        fields = "id,username,followers_count,media_count,follows_count,profile_picture_url,biography,name"
        url = f"{self.base_url}/{self.ig_user_id}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                url,
                params=self._params({"fields": fields}),
            )
            self._raise_for_status(resp, url)
            return resp.json()

    async def create_image_container(
        self,
        image_url: str,
        caption: str = "",
    ) -> str:
        """Create a media container for a single image.

        Returns the container id to be passed to ``publish_container``.
        """
        if not image_url:
            raise ValueError("image_url is required")
        url = f"{self.base_url}/{self.ig_user_id}/media"
        payload = self._params(
            {
                "image_url": image_url,
                "caption": caption[:MAX_CAPTION_CHARS],
            }
        )
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, data=payload)
            self._raise_for_status(resp, url)
            data = resp.json() or {}
            container_id = data.get("id")
            if not container_id:
                raise InstagramAPIError(
                    resp.status_code,
                    "create_image_container returned no id",
                    url,
                )
            return str(container_id)

    async def create_video_container(
        self,
        video_url: str,
        caption: str = "",
    ) -> str:
        """Create a media container for a single video.

        Returns the container id to be passed to ``publish_container``.
        """
        if not video_url:
            raise ValueError("video_url is required")
        url = f"{self.base_url}/{self.ig_user_id}/media"
        payload = self._params(
            {
                "video_url": video_url,
                "media_type": "VIDEO",
                "caption": caption[:MAX_CAPTION_CHARS],
            }
        )
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, data=payload)
            self._raise_for_status(resp, url)
            data = resp.json() or {}
            container_id = data.get("id")
            if not container_id:
                raise InstagramAPIError(
                    resp.status_code,
                    "create_video_container returned no id",
                    url,
                )
            return str(container_id)

    async def create_carousel_item(self, image_url: str) -> str:
        """Create a child media container for a carousel (image item).

        The ``is_carousel_item`` flag marks this container as a child that
        will be referenced by a parent carousel container.
        """
        if not image_url:
            raise ValueError("image_url is required")
        url = f"{self.base_url}/{self.ig_user_id}/media"
        payload = self._params(
            {
                "image_url": image_url,
                "is_carousel_item": "true",
            }
        )
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, data=payload)
            self._raise_for_status(resp, url)
            data = resp.json() or {}
            container_id = data.get("id")
            if not container_id:
                raise InstagramAPIError(
                    resp.status_code,
                    "create_carousel_item returned no id",
                    url,
                )
            return str(container_id)

    async def create_carousel_container(
        self,
        children_ids: list[str],
        caption: str = "",
    ) -> str:
        """Create a parent carousel container referencing child item containers.

        ``children_ids`` must be a list of container ids returned by
        ``create_carousel_item``. Instagram allows 2-10 items per carousel.
        """
        if not children_ids:
            raise ValueError("children_ids must contain at least one item")
        if len(children_ids) > 10:
            raise ValueError("Instagram carousels support a maximum of 10 items")
        for cid in children_ids:
            _validate_id(cid, "children_id")

        url = f"{self.base_url}/{self.ig_user_id}/media"
        payload = self._params(
            {
                "media_type": "CAROUSEL",
                "children": ",".join(children_ids),
                "caption": caption[:MAX_CAPTION_CHARS],
            }
        )
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, data=payload)
            self._raise_for_status(resp, url)
            data = resp.json() or {}
            container_id = data.get("id")
            if not container_id:
                raise InstagramAPIError(
                    resp.status_code,
                    "create_carousel_container returned no id",
                    url,
                )
            return str(container_id)

    async def publish_container(self, creation_id: str) -> str:
        """Publish a previously created media container.

        Returns the published media id.
        """
        creation_id = _validate_id(creation_id, "creation_id")
        url = f"{self.base_url}/{self.ig_user_id}/media_publish"
        payload = self._params({"creation_id": creation_id})
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, data=payload)
            self._raise_for_status(resp, url)
            data = resp.json() or {}
            media_id = data.get("id")
            if not media_id:
                raise InstagramAPIError(
                    resp.status_code,
                    "media_publish returned no id",
                    url,
                )
            return str(media_id)

    async def check_container_status(self, container_id: str) -> str:
        """Check the processing status of a media container.

        Returns the ``status_code`` field: ``IN_PROGRESS``, ``FINISHED``,
        or ``ERROR``.
        """
        container_id = _validate_id(container_id, "container_id")
        url = f"{self.base_url}/{container_id}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                url,
                params=self._params({"fields": "status_code"}),
            )
            self._raise_for_status(resp, url)
            data = resp.json() or {}
            return str(data.get("status_code") or "UNKNOWN")

    async def get_media_insights(self, media_id: str) -> dict[str, Any]:
        """Fetch insights for a single published media object.

        Available metrics depend on media type (image, video, carousel,
        reel, story).
        """
        media_id = _validate_id(media_id, "media_id")
        url = f"{self.base_url}/{media_id}/insights"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, params=self._params())
            self._raise_for_status(resp, url)
            return resp.json()

    async def get_account_insights(
        self,
        metric: str = "impressions",
        period: str = "day",
    ) -> dict[str, Any]:
        """Fetch account-level insights for the Instagram Business account.

        Common metrics: ``impressions``, ``reach``, ``follower_count``,
        ``profile_views``. Common periods: ``day``, ``week``, ``days_28``,
        ``lifetime``.
        """
        if not metric:
            raise ValueError("metric is required")
        url = f"{self.base_url}/{self.ig_user_id}/insights"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                url,
                params=self._params({"metric": metric, "period": period}),
            )
            self._raise_for_status(resp, url)
            return resp.json()

    # ── Feature 1: Publishing quota check ────────────────────────────────

    async def get_publishing_limit(self) -> dict[str, Any]:
        """Check the 24-hour content publishing limit.

        Returns quota usage including remaining posts. Instagram allows
        25 published posts per 24-hour rolling window. Call this before
        attempting to publish to avoid failed posts.

        Example response::

            {"data": [{"quota_usage": [...], "config": {"quota_total": 25}}]}
        """
        url = f"{self.base_url}/{self.ig_user_id}/content_publishing_limit"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, params=self._params())
            self._raise_for_status(resp, url)
            return resp.json()

    async def get_remaining_publish_quota(self) -> int:
        """Return the number of posts remaining in the current 24h window.

        Returns 25 if no usage data is available (fresh account).
        """
        try:
            data = await self.get_publishing_limit()
            usage_data = (data.get("data") or [{}])[0]
            quota_usage = usage_data.get("quota_usage") or []
            used = 0
            for item in quota_usage:
                if item.get("metric") == "publish_count":
                    used = int(item.get("value", 0))
                    break
            config = usage_data.get("config") or {}
            total = int(config.get("quota_total", 25))
            return max(0, total - used)
        except Exception:
            return 25  # fail open — assume full quota

    # ── Feature 2: Comment management ────────────────────────────────────

    async def list_comments(
        self,
        media_id: str,
        limit: int = 50,
    ) -> dict[str, Any]:
        """List comments on a published media object.

        Requires ``instagram_manage_comments`` permission.
        Returns up to ``limit`` comments with id, text, username, timestamp.
        """
        media_id = _validate_id(media_id, "media_id")
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        url = f"{self.base_url}/{media_id}/comments"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                url,
                params=self._params({"fields": "id,text,username,timestamp,like_count", "limit": limit}),
            )
            self._raise_for_status(resp, url)
            return resp.json()

    async def reply_to_comment(
        self,
        comment_id: str,
        message: str,
    ) -> dict[str, Any]:
        """Reply to an existing comment.

        Requires ``instagram_manage_comments`` permission.
        Returns the new reply comment object.
        """
        comment_id = _validate_id(comment_id, "comment_id")
        if not message or not message.strip():
            raise ValueError("message is required")
        url = f"{self.base_url}/{comment_id}/replies"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                data=self._params({"message": message[:1000]}),
            )
            self._raise_for_status(resp, url)
            return resp.json()

    async def hide_comment(self, comment_id: str, hide: bool = True) -> dict[str, Any]:
        """Hide or unhide a comment.

        Requires ``instagram_manage_comments`` permission.
        """
        comment_id = _validate_id(comment_id, "comment_id")
        url = f"{self.base_url}/{comment_id}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                data=self._params({"hidden": "true" if hide else "false"}),
            )
            self._raise_for_status(resp, url)
            return resp.json()

    async def delete_comment(self, comment_id: str) -> bool:
        """Delete a comment.

        Requires ``instagram_manage_comments`` permission.
        Returns True if deleted successfully.
        """
        comment_id = _validate_id(comment_id, "comment_id")
        url = f"{self.base_url}/{comment_id}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.delete(url, params=self._params())
            self._raise_for_status(resp, url)
            return True

    # ── Feature 3: Auto-poll container status ────────────────────────────

    async def wait_for_container_ready(
        self,
        container_id: str,
        timeout: float = 120.0,
        interval: float = 3.0,
    ) -> str:
        """Poll container status until FINISHED or ERROR.

        Video and reel containers process asynchronously on Instagram's
        side. This method polls ``check_container_status`` at regular
        intervals until the container is ready or an error occurs.

        Raises ``InstagramAPIError`` if the container ends in ERROR state
        or ``TimeoutError`` if ``timeout`` seconds elapse.
        """
        import time

        container_id = _validate_id(container_id, "container_id")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = await self.check_container_status(container_id)
            if status == "FINISHED":
                return status
            if status == "ERROR":
                raise InstagramAPIError(
                    400,
                    f"Container {container_id} processing failed with ERROR status",
                    f"container:{container_id}",
                )
            await asyncio.sleep(interval)
        raise TimeoutError(
            f"Container {container_id} did not finish processing within {timeout}s"
        )

    async def create_and_publish_video(
        self,
        video_url: str,
        caption: str = "",
        timeout: float = 120.0,
    ) -> str:
        """One-call video publish: create container, wait for ready, publish.

        Handles the async container processing automatically. Returns the
        published media id.
        """
        container_id = await self.create_video_container(video_url, caption)
        await self.wait_for_container_ready(container_id, timeout=timeout)
        return await self.publish_container(container_id)

    # ── Feature 4: Story builder with stickers/links/mentions ────────────

    async def create_story_container(
        self,
        media_url: str,
        media_type: str = "IMAGE",
        link: str | None = None,
        alt_text: str | None = None,
    ) -> str:
        """Create a story media container via the Graph API.

        Instagram stories support images and videos. The optional ``link``
        adds a swipe-up link (requires 10K+ followers or a verified account
        for the link sticker). ``alt_text`` improves accessibility.

        Returns the container id to be passed to ``publish_container``.
        """
        if not media_url:
            raise ValueError("media_url is required")
        if media_type not in ("IMAGE", "VIDEO"):
            raise ValueError("media_type must be 'IMAGE' or 'VIDEO'")

        payload: dict[str, Any] = {
            "media_type": media_type,
            "image_url" if media_type == "IMAGE" else "video_url": media_url,
        }
        if link:
            payload["link"] = link
        if alt_text:
            payload["alt_text"] = alt_text

        url = f"{self.base_url}/{self.ig_user_id}/media"
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, data=self._params(payload))
            self._raise_for_status(resp, url)
            data = resp.json() or {}
            container_id = data.get("id")
            if not container_id:
                raise InstagramAPIError(
                    resp.status_code,
                    "create_story_container returned no id",
                    url,
                )
            return str(container_id)

    async def publish_story(
        self,
        media_url: str,
        media_type: str = "IMAGE",
        link: str | None = None,
        alt_text: str | None = None,
        timeout: float = 120.0,
    ) -> str:
        """One-call story publish: create container, wait (if video), publish.

        For video stories, polls the container until processing is complete.
        Returns the published media id.
        """
        container_id = await self.create_story_container(
            media_url=media_url,
            media_type=media_type,
            link=link,
            alt_text=alt_text,
        )
        if media_type == "VIDEO":
            await self.wait_for_container_ready(container_id, timeout=timeout)
        return await self.publish_container(container_id)

    # ── Feature 5: Mentions tracking (tagged_media) ──────────────────────

    async def get_tagged_media(
        self,
        fields: str = "id,caption,media_type,media_url,permalink,timestamp,username",
        limit: int = 25,
    ) -> dict[str, Any]:
        """Fetch media where the account is tagged (brand mentions/UGC).

        Requires ``instagram_manage_insights`` permission.
        Returns posts where the Instagram Business account is tagged,
        useful for discovering user-generated content and brand mentions.
        """
        url = f"{self.base_url}/{self.ig_user_id}/tagged_media"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                url,
                params=self._params({"fields": fields, "limit": limit}),
            )
            self._raise_for_status(resp, url)
            return resp.json()

    async def get_recent_mentions(
        self,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Return a flat list of recent tagged media (brand mentions).

        Convenience wrapper around ``get_tagged_media`` that extracts
        the ``data`` array from the Graph API response.
        """
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        result = await self.get_tagged_media(limit=limit)
        return result.get("data") or []
