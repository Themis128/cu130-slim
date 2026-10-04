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

import asyncio
import logging
import re
from collections.abc import Callable
from typing import Any

import httpx

# X API v2 media upload (docs.x.com/x-api/media/introduction). The legacy
# v1.1 ``upload.twitter.com/1.1/media/upload.json`` endpoint is superseded;
# v2 accepts an OAuth 2.0 user token with the ``media.write`` scope (or an
# OAuth 1.0a user-context signature).
X_API_BASE = "https://api.x.com/2"
X_MEDIA_UPLOAD_URL = f"{X_API_BASE}/media/upload"
X_MEDIA_METADATA_URL = f"{X_API_BASE}/media/metadata"
# Backwards-compatible alias (older imports/tests).
TWITTER_MEDIA_UPLOAD_URL = X_MEDIA_UPLOAD_URL
# Chunked upload: keep each APPEND segment <= 5 MB (server max 8 MB).
X_MEDIA_CHUNK_BYTES = 4 * 1024 * 1024
# Upper bound on waiting for async (video/GIF) processing after FINALIZE.
X_MEDIA_PROCESSING_TIMEOUT_S = 600.0
X_ALT_TEXT_MAX = 1000

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


def _is_credits_depleted(text: str) -> bool:
    """402 body markers for an empty pay-per-use credit balance."""
    low = (text or "").lower()
    return any(m in low for m in ("credits-depleted", "creditsdepleted", "credits depleted", "/problems/credits"))


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
        headers: dict[str, str] | None = None,
    ):
        self.status_code = status_code
        self.response_text = response_text
        self.url = url
        # Lower-cased response headers (x-rate-limit-reset,
        # x-user-limit-24hour-reset, ...) so callers can reschedule.
        self.headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
        if message is None:
            message = f"Twitter API error {status_code} for {url}: {response_text[:400]}"
        super().__init__(message)


class TwitterAPIClient:
    """Async Twitter/X API v2 client for a single OAuth 2.0 access token.

    The client does not store decrypted tokens beyond the lifetime of the
    instance. Callers are responsible for encrypting tokens at rest.
    """

    def __init__(
        self,
        access_token: str,
        api_base: str = X_API_BASE,
        *,
        media_signer: Callable[[str, str, dict[str, str] | None], str] | None = None,
    ):
        """``media_signer(method, url, query_params)`` returns an OAuth 1.0a
        ``Authorization`` header used for the media endpoints instead of the
        Bearer token — for accounts whose OAuth 2.0 grant predates the
        ``media.write`` scope."""
        if not access_token:
            raise ValueError("Twitter access token is required")
        self.access_token = access_token
        self.api_base = api_base.rstrip("/")
        self.media_signer = media_signer

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    def _media_auth(self, method: str, url: str, params: dict[str, str] | None = None) -> dict[str, str]:
        if self.media_signer is not None:
            return {"Authorization": self.media_signer(method, url, params)}
        return {"Authorization": f"Bearer {self.access_token}"}

    def _media_url(self, path: str = "") -> str:
        return f"{self.api_base}/media/upload{path}"

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
        try:
            hdrs = {k: v for k, v in resp.headers.items() if k.lower().startswith("x-") or k.lower() == "retry-after"}
        except Exception:  # noqa: BLE001 — test doubles may lack headers
            hdrs = {}
        if resp.status_code == 402 and _is_credits_depleted(text):
            raise TwitterAPIError(
                status_code,
                text,
                url,
                message=(
                    "X API credits depleted (HTTP 402) — the developer account "
                    "balance is $0. X API is pay-per-use: every billed call, "
                    "including POST /2/tweets ($0.015, $0.20 with a URL), "
                    "/2/users/me and DM reads, is refused until credits are "
                    "added or auto-recharge is enabled at console.x.com. "
                    "Reconnecting the account does not help."
                ),
                headers=hdrs,
            )
        raise TwitterAPIError(status_code, text, url, headers=hdrs)

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

    @staticmethod
    def _media_id_from(body: dict[str, Any] | None) -> str:
        body = body or {}
        data = body.get("data") or {}
        return str(data.get("id") or body.get("media_id_string") or body.get("media_id") or "")

    async def upload_media(
        self,
        media_bytes: bytes,
        media_category: str = "tweet_image",
        mime_type: str = "image/jpeg",
        filename: str = "media",
    ) -> str:
        """Simple (one-shot) image upload; returns the media ID string.

        ``POST /2/media/upload`` (multipart: ``media`` + ``media_category``).
        X documents the simple upload for images and small files only — use
        :meth:`upload_media_chunked` for GIFs and video.
        """
        if not media_bytes:
            raise ValueError("media_bytes is empty")

        url = self._media_url()
        files = {"media": (filename, media_bytes, mime_type)}
        data = {"media_category": media_category}

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, headers=self._media_auth("POST", url), files=files, data=data)
            self._raise_for_status(resp, url)
            media_id = self._media_id_from(resp.json())
            if not media_id:
                raise TwitterAPIError(resp.status_code, "Media upload returned no media id", url)
            return media_id

    async def upload_media_chunked(
        self,
        media_bytes: bytes,
        media_type: str,
        media_category: str,
        chunk_size: int = X_MEDIA_CHUNK_BYTES,
        processing_timeout: float = X_MEDIA_PROCESSING_TIMEOUT_S,
    ) -> str:
        """Chunked upload (video / GIF / large media); returns the media ID.

        v2 flow per docs.x.com (dedicated paths — not ``command=INIT`` etc.):
        ``POST /2/media/upload/initialize`` (JSON) →
        ``POST /2/media/upload/{id}/append`` (multipart, ``segment_index``) →
        ``POST /2/media/upload/{id}/finalize`` → poll
        ``GET /2/media/upload?command=STATUS&media_id=…`` while
        ``processing_info`` is pending / in_progress.
        """
        if not media_bytes:
            raise ValueError("media_bytes is empty")
        if not 0 < chunk_size <= 8 * 1024 * 1024:
            raise ValueError("chunk_size must be between 1 byte and 8 MB")

        async with httpx.AsyncClient(timeout=120.0) as client:
            init_url = self._media_url("/initialize")
            resp = await client.post(
                init_url,
                headers={**self._media_auth("POST", init_url), "Content-Type": "application/json"},
                json={"media_type": media_type, "total_bytes": len(media_bytes), "media_category": media_category},
            )
            self._raise_for_status(resp, init_url)
            media_id = self._media_id_from(resp.json())
            if not media_id or not _TWEET_ID_RE.match(media_id):
                raise TwitterAPIError(resp.status_code, "Media initialize returned no media id", init_url)

            append_url = self._media_url(f"/{media_id}/append")
            for segment_index, offset in enumerate(range(0, len(media_bytes), chunk_size)):
                chunk = media_bytes[offset : offset + chunk_size]
                resp = await client.post(
                    append_url,
                    headers=self._media_auth("POST", append_url),
                    files={"media": ("chunk", chunk, "application/octet-stream")},
                    data={"segment_index": str(segment_index)},
                )
                self._raise_for_status(resp, append_url)

            finalize_url = self._media_url(f"/{media_id}/finalize")
            resp = await client.post(finalize_url, headers=self._media_auth("POST", finalize_url))
            self._raise_for_status(resp, finalize_url)
            info = ((resp.json() or {}).get("data") or {}).get("processing_info")

            loop = asyncio.get_running_loop()
            deadline = loop.time() + processing_timeout
            status_url = self._media_url()
            while info and info.get("state") in ("pending", "in_progress"):
                if loop.time() >= deadline:
                    raise TwitterAPIError(
                        0, f"media {media_id} still processing after {int(processing_timeout)}s", status_url,
                    )
                wait = min(max(float(info.get("check_after_secs") or 1), 1.0), 30.0)
                await asyncio.sleep(wait)
                params = {"command": "STATUS", "media_id": media_id}
                resp = await client.get(status_url, headers=self._media_auth("GET", status_url, params), params=params)
                self._raise_for_status(resp, status_url)
                info = ((resp.json() or {}).get("data") or {}).get("processing_info")

            if info and info.get("state") == "failed":
                err = info.get("error") or {}
                detail = err.get("message") or err.get("name") or "processing failed"
                raise TwitterAPIError(400, f"media {media_id} processing failed: {detail}", status_url)
            return media_id

    async def set_media_alt_text(self, media_id: str, alt_text: str) -> None:
        """Attach alt text to uploaded media (``POST /2/media/metadata``).

        X caps alt text at 1000 characters; longer text is truncated.
        Billed as "Media Metadata" under pay-per-use.
        """
        media_id = _validate_tweet_id(media_id)
        text = (alt_text or "").strip()[:X_ALT_TEXT_MAX]
        if not text:
            return
        url = X_MEDIA_METADATA_URL if self.api_base == X_API_BASE else f"{self.api_base}/media/metadata"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                headers={**self._media_auth("POST", url), "Content-Type": "application/json"},
                json={"id": media_id, "metadata": {"alt_text": {"text": text}}},
            )
            self._raise_for_status(resp, url)

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

    # ── Direct Messages (DM API v2) ──────────────────────────────────────
    # Requires OAuth 2.0 user-context with dm.read + dm.write scopes (and the
    # app permission "Read and write and Direct message"). Pay-per-use
    # billing: DM Event read $0.010/resource, DM create $0.015/request.
    # Rate limits: 15 sends/15min, 1,440/24h per user (docs.x.com rate limits).

    async def send_dm(self, participant_id: str, text: str) -> dict[str, Any]:
        """Send a one-to-one direct message to a user.

        Creates a new conversation if one doesn't exist; otherwise appends
        to the existing conversation.
        Requires ``dm.write`` scope.

        Args:
            participant_id: The numeric X user ID of the recipient.
            text: The message text (max 10,000 chars).

        Returns:
            API response with ``dm_conversation_id`` and ``dm_event_id``.
        """
        participant_id = _validate_user_id(participant_id)
        if not text or len(text) > 10000:
            raise ValueError("text is required and must be <= 10000 chars")

        url = f"{self.api_base}/dm_conversations/with/{participant_id}/messages"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=self._headers(), json={"text": text})
            self._raise_for_status(resp, url)
            return resp.json()

    async def send_dm_to_conversation(self, conversation_id: str, text: str) -> dict[str, Any]:
        """Send a message to an existing DM conversation.

        Requires ``dm.write`` scope.

        Args:
            conversation_id: The DM conversation ID.
            text: The message text (max 10,000 chars).
        """
        if not conversation_id:
            raise ValueError("conversation_id is required")
        if not text or len(text) > 10000:
            raise ValueError("text is required and must be <= 10000 chars")

        url = f"{self.api_base}/dm_conversations/{conversation_id}/messages"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=self._headers(), json={"text": text})
            self._raise_for_status(resp, url)
            return resp.json()

    async def list_dm_events(
        self,
        max_results: int = 50,
        event_types: str = "MessageCreate",
        dm_event_fields: str = "id,text,created_at,sender_id,dm_conversation_id",
    ) -> dict[str, Any]:
        """List DM events (messages received and sent).

        Requires ``dm.read`` scope. Returns recent DM events with pagination.

        Args:
            max_results: Number of events to fetch (1-100, default 50).
            event_types: Comma-separated event types (MessageCreate, etc).
            dm_event_fields: Fields to include in the response.
        """
        if not 1 <= max_results <= 100:
            raise ValueError("max_results must be between 1 and 100")

        url = f"{self.api_base}/dm_events"
        params = {
            "max_results": max_results,
            "event_types": event_types,
            "dm.event.fields": dm_event_fields,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=self._headers(), params=params)
            self._raise_for_status(resp, url)
            return resp.json()

    async def get_dm_conversation_events(
        self,
        conversation_id: str,
        max_results: int = 50,
        dm_event_fields: str = "id,text,created_at,sender_id",
    ) -> dict[str, Any]:
        """Get events for a specific DM conversation.

        Requires ``dm.read`` scope.
        """
        if not conversation_id:
            raise ValueError("conversation_id is required")
        if not 1 <= max_results <= 100:
            raise ValueError("max_results must be between 1 and 100")

        url = f"{self.api_base}/dm_conversations/{conversation_id}/dm_events"
        params = {
            "max_results": max_results,
            "dm.event.fields": dm_event_fields,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=self._headers(), params=params)
            self._raise_for_status(resp, url)
            return resp.json()

    async def delete_dm(self, event_id: str) -> bool:
        """Delete a DM event (message).

        Requires ``dm.write`` scope.
        """
        if not event_id:
            raise ValueError("event_id is required")

        url = f"{self.api_base}/dm_events/{event_id}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.delete(url, headers=self._headers())
            if resp.status_code == 204:
                return True
            self._raise_for_status(resp, url)
            return True
