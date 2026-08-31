"""Standalone Twitter/X API v2 client.

This module wraps the Twitter/X API calls needed for the Cloudless social
stack without depending on the database or Celery worker. It is consumed by:

- ``app/api/twitter.py`` for the Twitter endpoints
- ``app/services/publishing.py`` / analytics sync
- One-off scripts and n8n webhooks that need direct Twitter API access

Twitter/X API v2 uses OAuth 2.0 PKCE user access tokens (Bearer). There is no
business vs personal distinction for posting; the same endpoints work for all
account types.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

TWITTER_MEDIA_UPLOAD_URL = "https://upload.twitter.com/1.1/media/upload.json"

logger = logging.getLogger(__name__)

# Twitter tweet IDs are numeric (snowflake) strings.
_TWEET_ID_RE = re.compile(r"^\d{1,30}$")
# Twitter user IDs are numeric strings.
_USER_ID_RE = re.compile(r"^\d{1,30}$")


def _sanitize_log_text(text: str, max_len: int = 400) -> str:
    """Sanitize API response text for safe logging -- strips newlines/control chars."""
    # Replace newlines and carriage returns to prevent log injection
    cleaned = text.replace("\n", "\\n").replace("\r", "\\r")
    # Strip other control characters (except tab)
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", cleaned)
    return cleaned[:max_len]


def _validate_tweet_id(tweet_id: str) -> str:
    """Validate a Twitter tweet ID to prevent injection via crafted identifiers."""
    tweet_id = tweet_id.strip()
    if not tweet_id:
        raise ValueError("Tweet ID is empty")
    if not _TWEET_ID_RE.match(tweet_id):
        raise ValueError(f"Invalid Twitter tweet ID format: {tweet_id[:80]}")
    return tweet_id


def _validate_user_id(user_id: str) -> str:
    """Validate a Twitter user ID to prevent injection via crafted identifiers."""
    user_id = user_id.strip()
    if not user_id:
        raise ValueError("User ID is empty")
    if not _USER_ID_RE.match(user_id):
        raise ValueError(f"Invalid Twitter user ID format: {user_id[:80]}")
    return user_id


class TwitterAPIError(Exception):
    """Raised when a Twitter/X API call fails with a non-success status.

    Twitter 5xx responses are surfaced as 502/503 gateway errors so the
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
            message = f"Twitter API error {status_code} for {url}: {response_text[:400]}"
        super().__init__(message)


class TwitterAPIClient:
    """Async Twitter/X API v2 client for a single OAuth 2.0 access token.

    The client does not store decrypted tokens beyond the lifetime of the
    instance. Callers are responsible for encrypting tokens at rest.
    """

    def __init__(self, access_token: str, api_base: str = "https://api.x.com/2"):
        if not access_token:
            raise ValueError("Twitter access token is required")
        self.access_token = access_token
        self.api_base = api_base.rstrip("/")

    def _headers(self) -> dict[str, str]:
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
        """Raise ``TwitterAPIError`` for any non-2xx response."""
        if resp.status_code < 400:
            return
        status_code = self._map_status_code(resp.status_code)
        text = _sanitize_log_text(resp.text)
        safe_url = _sanitize_log_text(url)
        logger.error("Twitter API error %s for %s: %s", status_code, safe_url, text)
        raise TwitterAPIError(status_code, text, url)

    def _log_api_error(self, url: str, resp: httpx.Response) -> None:
        """Log the response body for a failed Twitter API call."""
        if resp.status_code >= 400:
            safe_url = _sanitize_log_text(url)
            logger.error(
                "Twitter API call to %s failed: HTTP %s: %s",
                safe_url,
                resp.status_code,
                _sanitize_log_text(resp.text),
            )

    async def validate_token(self) -> dict[str, Any]:
        """Validate the access token and return the authenticated user info.

        Calls ``GET /2/users/me``. Raises ``TwitterAPIError`` on invalid,
        expired, or upstream error.
        """
        url = f"{self.api_base}/users/me"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=self._headers())
            self._raise_for_status(resp, url)
            return resp.json()

    async def create_tweet(
        self,
        text: str,
        media_ids: list[str] | None = None,
        reply_tweet_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a tweet with optional media attachments and/or reply.

        Calls ``POST /2/tweets``. ``media_ids`` should be IDs returned by
        ``upload_media``. ``reply_tweet_id`` starts a reply thread.
        Returns the raw API response containing the tweet ``id`` and ``text``.
        """
        if not text or not text.strip():
            raise ValueError("Tweet text is required")

        payload: dict[str, Any] = {"text": text}
        if media_ids:
            if len(media_ids) > 4:
                raise ValueError("A tweet can attach at most 4 media items")
            payload["media"] = {"media_ids": media_ids}
        if reply_tweet_id:
            payload["reply"] = {"in_reply_to_tweet_id": reply_tweet_id}

        url = f"{self.api_base}/tweets"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=self._headers(), json=payload)
            self._raise_for_status(resp, url)
            return resp.json()

    async def delete_tweet(self, tweet_id: str) -> bool:
        """Delete a tweet by ID.

        Calls ``DELETE /2/tweets/{id}``. Returns ``True`` if the tweet was
        deleted, ``False`` if the tweet was already gone (404).
        """
        tweet_id = _validate_tweet_id(tweet_id)
        url = f"{self.api_base}/tweets/{tweet_id}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.delete(url, headers=self._headers())
            if resp.status_code == 404:
                return False
            self._raise_for_status(resp, url)
            data = resp.json() or {}
            return bool(data.get("deleted"))

    async def get_tweet(self, tweet_id: str) -> dict[str, Any]:
        """Fetch a single tweet by ID.

        Calls ``GET /2/tweets/{id}``. Returns the raw API response.
        """
        tweet_id = _validate_tweet_id(tweet_id)
        url = f"{self.api_base}/tweets/{tweet_id}"
        params = {"tweet.fields": "created_at,public_metrics,entities"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=self._headers(), params=params)
            self._raise_for_status(resp, url)
            return resp.json()

    async def upload_media(
        self,
        media_bytes: bytes,
        media_category: str = "tweet_image",
    ) -> str:
        """Upload media to Twitter and return the media ID string.

        Uses the v1.1 ``media/upload`` endpoint with multipart form data.
        The returned ``media_id_string`` is suitable for passing to
        ``create_tweet`` via the ``media_ids`` parameter.
        """
        if not media_bytes:
            raise ValueError("media_bytes is empty")

        headers = {"Authorization": f"Bearer {self.access_token}"}
        files = {"media": media_bytes}
        data = {"media_category": media_category}

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                TWITTER_MEDIA_UPLOAD_URL,
                headers=headers,
                files=files,
                data=data,
            )
            self._raise_for_status(resp, TWITTER_MEDIA_UPLOAD_URL)
            body = resp.json() or {}
            media_id = body.get("media_id_string") or str(body.get("media_id") or "")
            if not media_id:
                raise TwitterAPIError(
                    resp.status_code,
                    "Media upload returned no media_id",
                    TWITTER_MEDIA_UPLOAD_URL,
                )
            return media_id

    async def get_user_tweets(
        self,
        user_id: str,
        max_results: int = 10,
    ) -> dict[str, Any]:
        """Fetch recent tweets for a user.

        Calls ``GET /2/users/{id}/tweets``. ``max_results`` must be between 5
        and 100. Returns the raw API response containing ``data`` and ``meta``.
        """
        user_id = _validate_user_id(user_id)
        if not 5 <= max_results <= 100:
            raise ValueError("max_results must be between 5 and 100")

        url = f"{self.api_base}/users/{user_id}/tweets"
        params = {
            "max_results": max_results,
            "tweet.fields": "created_at,public_metrics,entities",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=self._headers(), params=params)
            self._raise_for_status(resp, url)
            return resp.json()
