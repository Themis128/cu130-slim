"""Standalone Facebook Graph API client.

This module wraps the Facebook Graph API calls needed for the Cloudless social
stack without depending on the database or Celery worker. It is consumed by:

- ``app/api/facebook.py`` for the Facebook endpoints
- ``app/services/publishing.py`` / analytics sync
- One-off scripts and n8n webhooks that need direct Facebook API access

Facebook uses two kinds of access tokens:

- **User tokens** identify a person and are used to discover Pages.
- **Page tokens** identify a Facebook Page and are required to post as that
  Page. A long-lived Page token is obtained by exchanging a short-lived user
  token for a long-lived user token, then requesting ``/me/accounts`` with the
  long-lived user token.

API reference: https://developers.facebook.com/docs/graph-api
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

FACEBOOK_GRAPH_BASE = "https://graph.facebook.com"
DEFAULT_API_VERSION = "v25.0"
DEFAULT_TIMEOUT = 60.0

logger = logging.getLogger(__name__)

# Facebook object ids are numeric strings, sometimes with a prefix like
# ``page_id_post_id``. We validate to prevent SSRF via crafted identifiers.
_FB_ID_RE = re.compile(r"^[0-9]{1,64}(_[0-9]{1,64})?$")


def _validate_id(value: str, label: str = "id") -> str:
    """Validate a Facebook object id to prevent SSRF via crafted identifiers."""
    value = value.strip()
    if not value:
        raise ValueError(f"Facebook {label} is empty")
    if not _FB_ID_RE.match(value):
        raise ValueError(f"Invalid Facebook {label} format: {value[:80]}")
    return value


def _sanitize_log_text(text: str, max_len: int = 400) -> str:
    """Sanitize API response text for safe logging -- strips newlines/control chars."""
    cleaned = text.replace("\n", "\\n").replace("\r", "\\r")
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", cleaned)
    return cleaned[:max_len]


class FacebookAPIError(Exception):
    """Raised when a Facebook Graph API call fails with a non-success status.

    Facebook 5xx responses are surfaced as 502/503 gateway errors so the
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
            message = f"Facebook API error {status_code} for {url}: {response_text[:400]}"
        super().__init__(message)

    @classmethod
    def from_response(cls, resp: httpx.Response, url: str) -> FacebookAPIError:
        """Build an error from a Graph API error payload if present."""
        try:
            body = resp.json() or {}
        except ValueError:
            body = {}
        error_obj = body.get("error") if isinstance(body, dict) else None
        if isinstance(error_obj, dict):
            fb_message = error_obj.get("message") or resp.text
            fb_type = error_obj.get("type")
            fb_code = error_obj.get("code")
            prefix = f"Facebook API error {resp.status_code}"
            if fb_type:
                prefix += f" ({fb_type})"
            if fb_code:
                prefix += f" [code {fb_code}]"
            return cls(resp.status_code, resp.text, url, message=f"{prefix}: {fb_message[:400]}")
        return cls(resp.status_code, resp.text, url)


class FacebookAPIClient:
    """Async Facebook Graph API client for a single Page access token.

    The client does not store decrypted tokens beyond the lifetime of the
    instance. Callers are responsible for encrypting tokens at rest.
    """

    def __init__(
        self,
        access_token: str,
        page_id: str,
        api_version: str = DEFAULT_API_VERSION,
    ):
        if not access_token:
            raise ValueError("Facebook access token is required")
        self.access_token = access_token
        self.page_id = _validate_id(page_id, "page_id")
        self.api_version = api_version.lstrip("/")
        self._base_url = f"{FACEBOOK_GRAPH_BASE}/{self.api_version}"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _params(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return query params with the access token attached."""
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
        """Raise ``FacebookAPIError`` for any non-2xx response."""
        if resp.status_code < 400:
            return
        status_code = self._map_status_code(resp.status_code)
        text = _sanitize_log_text(resp.text)
        safe_url = _sanitize_log_text(url)
        logger.error("Facebook API error %s for %s: %s", status_code, safe_url, text)
        err = FacebookAPIError.from_response(resp, url)
        err.status_code = status_code
        raise err

    def _url(self, path: str) -> str:
        """Build a fully-qualified Graph API URL for a path."""
        path = path.lstrip("/")
        return f"{self._base_url}/{path}"

    # ------------------------------------------------------------------
    # Token & account discovery
    # ------------------------------------------------------------------

    async def validate_token(self) -> dict:
        """Validate the access token via ``/me`` and return the profile payload.

        Raises ``FacebookAPIError`` on invalid, expired, or upstream error.
        """
        url = self._url("me")
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(
                url,
                params=self._params({"fields": "id,name"}),
            )
            self._raise_for_status(resp, url)
            return resp.json()

    async def get_pages(self) -> list[dict]:
        """Discover Facebook Pages the authenticated user can administer.

        Calls ``GET /me/accounts`` and returns the list of page dicts, each
        containing ``id``, ``name``, ``access_token``, and other fields.
        """
        url = self._url("me/accounts")
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(
                url,
                params=self._params({"fields": "id,name,access_token,category,tasks"}),
            )
            self._raise_for_status(resp, url)
            data = resp.json() or {}
            return data.get("data") or []

    async def exchange_long_lived_token(self, client_id: str, client_secret: str) -> str:
        """Exchange a short-lived user token for a long-lived user token.

        Returns the long-lived access token string. The caller should then
        pass it to ``get_long_lived_page_tokens`` to obtain Page tokens.
        """
        url = self._url("oauth/access_token")
        params = {
            "grant_type": "fb_exchange_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "fb_exchange_token": self.access_token,
        }
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(url, params=params)
            self._raise_for_status(resp, url)
            data = resp.json() or {}
            token = data.get("access_token")
            if not token:
                raise ValueError("Facebook did not return a long-lived access token")
            return str(token)

    async def get_long_lived_page_tokens(self, long_lived_user_token: str) -> list[dict]:
        """Discover Pages with long-lived Page tokens using a long-lived user token.

        Calls ``GET /{user-id}/accounts`` with the long-lived user token and
        returns the list of page dicts, each containing a long-lived
        ``access_token``.
        """
        # Resolve the user id from /me using the long-lived token.
        me_url = self._url("me")
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            me_resp = await client.get(
                me_url,
                params={"access_token": long_lived_user_token, "fields": "id,name"},
            )
            self._raise_for_status(me_resp, me_url)
            user_id = (me_resp.json() or {}).get("id")
            if not user_id:
                raise ValueError("Could not resolve Facebook user id from /me")

            url = self._url(f"{user_id}/accounts")
            resp = await client.get(
                url,
                params={
                    "access_token": long_lived_user_token,
                    "fields": "id,name,access_token,category,perms",
                },
            )
            self._raise_for_status(resp, url)
            data = resp.json() or {}
            return data.get("data") or []

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    async def create_post(self, message: str, link: str | None = None) -> dict:
        """Publish a text or link post to the Page feed.

        ``POST /{page_id}/feed`` with ``message`` and optional ``link``.
        Returns the Graph API response containing the new post ``id``.
        """
        if not message and not link:
            raise ValueError("Either message or link is required for a Facebook post")

        url = self._url(f"{self.page_id}/feed")
        payload: dict[str, Any] = {"message": message}
        if link:
            payload["link"] = link

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(url, params=self._params(), data=payload)
            self._raise_for_status(resp, url)
            return resp.json()

    async def create_photo_post(self, image_url: str, caption: str = "") -> dict:
        """Publish a photo post to the Page.

        ``POST /{page_id}/photos`` with ``url`` (public image URL) and
        ``caption``. Returns the Graph API response containing the new post
        ``id`` and ``post_id``.
        """
        if not image_url:
            raise ValueError("image_url is required for a Facebook photo post")

        url = self._url(f"{self.page_id}/photos")
        payload: dict[str, Any] = {"url": image_url}
        if caption:
            payload["caption"] = caption

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(url, params=self._params(), data=payload)
            self._raise_for_status(resp, url)
            return resp.json()

    async def create_video_post(self, video_url: str, description: str = "") -> dict:
        """Publish a video post to the Page.

        ``POST /{page_id}/videos`` with ``file_url`` (public video URL) and
        ``description``. Returns the Graph API response containing the new
        video ``id``.
        """
        if not video_url:
            raise ValueError("video_url is required for a Facebook video post")

        url = self._url(f"{self.page_id}/videos")
        payload: dict[str, Any] = {"file_url": video_url}
        if description:
            payload["description"] = description

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(url, params=self._params(), data=payload)
            self._raise_for_status(resp, url)
            return resp.json()

    async def create_multi_photo_post(
        self,
        image_urls: list[str],
        message: str = "",
        link: str | None = None,
    ) -> dict:
        """Publish a multi-photo (album) post to the Page feed.

        First uploads each photo as unpublished, then creates a feed post
        with the images attached via ``attached_media``. Returns the Graph
        API response containing the new post ``id``.
        """
        if not image_urls:
            raise ValueError("At least one image_url is required for a multi-photo post")

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            photo_ids: list[str] = []
            for image_url in image_urls[:10]:
                photo_url = self._url(f"{self.page_id}/photos")
                upload_payload = {"published": "false", "url": image_url}
                upload_resp = await client.post(photo_url, params=self._params(), data=upload_payload)
                self._raise_for_status(upload_resp, photo_url)
                photo_id = (upload_resp.json() or {}).get("id")
                if photo_id:
                    photo_ids.append(photo_id)

            if not photo_ids:
                raise FacebookAPIError(
                    status_code=400,
                    response_text="No Facebook photo uploads succeeded",
                    url=self._url(f"{self.page_id}/photos"),
                )

            import json as _json

            attached = [{"media_fbid": pid} for pid in photo_ids]
            feed_url = self._url(f"{self.page_id}/feed")
            payload: dict[str, Any] = {"message": message, "attached_media": _json.dumps(attached)}
            if link:
                payload["link"] = link

            resp = await client.post(feed_url, params=self._params(), data=payload)
            self._raise_for_status(resp, feed_url)
            return resp.json()

    # ------------------------------------------------------------------
    # Insights / analytics
    # ------------------------------------------------------------------

    async def get_page_insights(
        self,
        metric: str = "page_impressions_unique",
        period: str = "day",
        since: str | None = None,
        until: str | None = None,
    ) -> dict:
        """Fetch Page-level insights for a given metric.

        ``GET /{page_id}/insights`` with ``metric``, ``period``, and optional
        ``since``/``until`` date boundaries (YYYY-MM-DD or unix timestamps).
        Returns the raw Graph API insights payload.
        """
        if not metric:
            raise ValueError("metric is required for Facebook page insights")

        url = self._url(f"{self.page_id}/insights")
        params: dict[str, Any] = {"metric": metric, "period": period}
        if since is not None:
            params["since"] = since
        if until is not None:
            params["until"] = until

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(url, params=self._params(params))
            self._raise_for_status(resp, url)
            return resp.json()

    async def get_post_insights(self, post_id: str) -> dict:
        """Fetch insights for a single post.

        ``GET /{post_id}/insights`` returning post-level metrics such as
        ``post_impressions`` and ``post_engaged_users``.
        """
        post_id = _validate_id(post_id, "post_id")
        url = self._url(f"{post_id}/insights")
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(
                url,
                params=self._params({"metric": "post_impressions,post_engaged_users"}),
            )
            self._raise_for_status(resp, url)
            return resp.json()

    # ------------------------------------------------------------------
    # Deletion
    # ------------------------------------------------------------------

    async def delete_post(self, post_id: str) -> bool:
        """Delete a post by id. Returns ``True`` on success.

        ``DELETE /{post_id}`` with the Page access token.
        """
        post_id = _validate_id(post_id, "post_id")
        url = self._url(post_id)
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.delete(url, params=self._params())
            self._raise_for_status(resp, url)
            data = resp.json() or {}
            return bool(data.get("success", True))
