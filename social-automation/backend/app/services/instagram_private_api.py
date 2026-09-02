"""Instagram Private API client (aiograpi-rest sidecar).

This module wraps the ``aiograpi-rest`` Docker sidecar, which exposes
Instagram's private mobile API as a RESTful HTTP service.  It enables
profile writes (biography, profile picture, full name, external URL,
phone number, email) that the official Instagram Graph API does not
support.

The sidecar runs as a separate Docker container (``instagram-private-api``
in ``docker-compose.yml``) on port 8000 internally, exposed as 8011 on
the host.  All requests require an ``X-Session-ID`` header that
identifies a logged-in Instagram session.

Session lifecycle:
    1. ``login(username, password)`` → returns a session_id
    2. Store session_id in ``social_accounts.meta_data`` for reuse
    3. Subsequent calls pass session_id via the ``X-Session-ID`` header
    4. If session expires, call ``login()`` again to get a new session_id

API reference: https://subzeroid.github.io/aiograpi-rest/
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 60.0

# Greek locale and timezone (UTC+3 = 10800 seconds) to reduce challenge risk.
DEFAULT_LOCALE = "el_GR"
DEFAULT_TIMEZONE = "10800"


class InstagramPrivateAPIError(Exception):
    """Raised when the aiograpi-rest sidecar returns a non-success status."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Instagram Private API error {status_code}: {detail}")


class InstagramPrivateAPIClient:
    """HTTP client for the aiograpi-rest sidecar.

    All methods accept a ``session_id`` that identifies a logged-in
    Instagram session.  Use :meth:`login` to obtain one.
    """

    def __init__(self, base_url: str, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def _headers(self, session_id: str | None = None) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if session_id:
            headers["X-Session-ID"] = session_id
        return headers

    def _raise_for_status(self, response: httpx.Response) -> dict[str, Any]:
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except Exception:
                detail = response.text
            raise InstagramPrivateAPIError(response.status_code, str(detail)[:500])
        try:
            return response.json()
        except Exception:
            return {}

    # ── Authentication ────────────────────────────────────────────────────

    async def login(
        self,
        username: str,
        password: str,
        verification_code: str | None = None,
        proxy: str | None = None,
        locale: str = DEFAULT_LOCALE,
        timezone: str = DEFAULT_TIMEZONE,
    ) -> dict[str, Any]:
        """Log in to Instagram and return the session info.

        On success, returns a dict with ``session_id``.  If Instagram
        requires 2FA, the response includes ``two_factor_required: True``
        and the caller must retry with ``verification_code``.

        Pass ``proxy`` as a mobile/residential proxy URL to reduce the
        chance of a challenge_required error from a datacenter IP.
        ``locale`` and ``timezone`` should match the account's real
        location (defaults: Greek locale, UTC+3).
        """
        data: dict[str, Any] = {
            "username": username,
            "password": password,
            "locale": locale,
            "timezone": timezone,
        }
        if verification_code:
            data["verification_code"] = verification_code
        if proxy:
            data["proxy"] = proxy

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/auth/login",
                data=data,
                headers={"Accept": "application/json"},
            )
            # The sidecar returns the session_id as a plain string on success.
            if resp.status_code == 200:
                try:
                    body = resp.json()
                except Exception:
                    body = {}
                if isinstance(body, str):
                    return {"session_id": body, "logged_in": True}
                if isinstance(body, bool) and body:
                    session_id = resp.headers.get("x-session-id", "")
                    return {"session_id": session_id, "logged_in": True}
                return body
            # Error: could be challenge_required or two_factor_required
            try:
                error_body = resp.json()
            except Exception:
                error_body = {"detail": resp.text}

            # Detect challenge / 2FA from error response
            exc_type = error_body.get("exc_type", "")
            if exc_type == "ChallengeRequired":
                return {
                    "logged_in": False,
                    "challenge_required": True,
                    "two_factor_required": False,
                    "message": error_body.get("hint", "Instagram challenge required."),
                    "last_json": error_body.get("last_json", ""),
                }
            if exc_type == "TwoFactorRequired":
                return {
                    "logged_in": False,
                    "challenge_required": False,
                    "two_factor_required": True,
                    "message": error_body.get("hint", "Instagram 2FA required."),
                }
            raise InstagramPrivateAPIError(resp.status_code, str(error_body.get("detail", resp.text))[:500])

    async def login_by_sessionid(
        self,
        session_id: str,
        proxy: str | None = None,
        locale: str = DEFAULT_LOCALE,
        timezone: str = DEFAULT_TIMEZONE,
    ) -> dict[str, Any]:
        """Log in using an existing Instagram sessionid cookie value."""
        data: dict[str, Any] = {
            "sessionid": session_id,
            "locale": locale,
            "timezone": timezone,
        }
        if proxy:
            data["proxy"] = proxy
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/auth/login/by/sessionid",
                data=data,
                headers={"Accept": "application/json"},
            )
            return self._raise_for_status(resp)

    async def challenge_resolve(
        self,
        session_id: str,
        last_json: str,
        security_code: str,
    ) -> dict[str, Any]:
        """Resolve an Instagram login challenge with a security code.

        After ``login()`` returns ``challenge_required: True``, call this
        with the SMS/email code Instagram sent to the account owner.
        """
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/auth/challenge/resolve",
                data={
                    "last_json": last_json if isinstance(last_json, str) else json.dumps(last_json),
                    "security_code": security_code,
                },
                headers=self._headers(session_id),
            )
            return self._raise_for_status(resp)

    async def get_settings(self, session_id: str) -> dict[str, Any]:
        """Get the client settings (can be saved for session restore)."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(
                f"{self._base_url}/auth/settings",
                headers=self._headers(session_id),
            )
            return self._raise_for_status(resp)

    async def set_settings(
        self,
        settings_json: str,
        proxy: str | None = None,
        locale: str | None = None,
        timezone: str | None = None,
    ) -> dict[str, Any]:
        """Import client settings to restore a session without password."""
        data: dict[str, Any] = {"settings": settings_json}
        if proxy is not None:
            data["proxy"] = proxy
        if locale is not None:
            data["locale"] = locale
        if timezone is not None:
            data["timezone"] = timezone
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.patch(
                f"{self._base_url}/auth/settings",
                data=data,
                headers={"Accept": "application/json"},
            )
            return self._raise_for_status(resp)

    # ── Profile reads ─────────────────────────────────────────────────────

    async def get_account(self, session_id: str) -> dict[str, Any]:
        """Get the current account's full profile (biography, email, phone, etc.)."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(
                f"{self._base_url}/account",
                headers=self._headers(session_id),
            )
            return self._raise_for_status(resp)

    # ── Profile writes ────────────────────────────────────────────────────

    async def update_account(
        self,
        session_id: str,
        *,
        biography: str | None = None,
        external_url: str | None = None,
        full_name: str | None = None,
        username: str | None = None,
        phone_number: str | None = None,
        email: str | None = None,
    ) -> dict[str, Any]:
        """Update one or more account profile fields.

        Only the fields you pass will be changed.  All fields are optional.
        """
        data: dict[str, str] = {}
        if biography is not None:
            data["biography"] = biography
        if external_url is not None:
            data["external_url"] = external_url
        if full_name is not None:
            data["full_name"] = full_name
        if username is not None:
            data["username"] = username
        if phone_number is not None:
            data["phone_number"] = phone_number
        if email is not None:
            data["email"] = email

        if not data:
            raise ValueError("At least one field must be provided to update_account")

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.patch(
                f"{self._base_url}/account",
                data=data,
                headers=self._headers(session_id),
            )
            return self._raise_for_status(resp)

    async def update_biography(self, session_id: str, biography: str) -> dict[str, Any]:
        """Update only the biography text."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.patch(
                f"{self._base_url}/account/biography",
                data={"biography": biography},
                headers=self._headers(session_id),
            )
            return self._raise_for_status(resp)

    async def update_external_url(
        self, session_id: str, external_url: str
    ) -> dict[str, Any]:
        """Set the bio link (external URL)."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.patch(
                f"{self._base_url}/account/external-url",
                data={"external_url": external_url},
                headers=self._headers(session_id),
            )
            return self._raise_for_status(resp)

    async def update_profile_picture(
        self, session_id: str, image_bytes: bytes, filename: str = "profile.jpg"
    ) -> dict[str, Any]:
        """Upload a new profile picture.

        The image must be a valid JPEG or PNG.  The sidecar handles
        resizing and upload to Instagram's servers.
        """
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.patch(
                f"{self._base_url}/account/picture",
                files={"picture": (filename, image_bytes, "image/jpeg")},
                headers=self._headers(session_id),
            )
            return self._raise_for_status(resp)

    # ── Privacy ───────────────────────────────────────────────────────────

    async def set_private(self, session_id: str) -> dict[str, Any]:
        """Switch the account to private mode."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.patch(
                f"{self._base_url}/account/privacy",
                data={"private": "true"},
                headers=self._headers(session_id),
            )
            return self._raise_for_status(resp)

    async def set_public(self, session_id: str) -> dict[str, Any]:
        """Switch the account to public mode."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.patch(
                f"{self._base_url}/account/privacy",
                data={"private": "false"},
                headers=self._headers(session_id),
            )
            return self._raise_for_status(resp)

    # ── Media publishing ──────────────────────────────────────────────────

    async def upload_photo(
        self,
        session_id: str,
        file_path: str,
        caption: str,
        location: str | None = None,
    ) -> dict[str, Any]:
        """Upload a single photo post.

        ``file_path`` must be a path accessible inside the sidecar container
        (e.g. ``/uploads/...`` when the uploads volume is mounted).
        Returns the media dict (includes ``id``, ``pk``, ``code``).
        """
        data: dict[str, Any] = {"file": file_path, "caption": caption}
        if location:
            data["location"] = location
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/photo/upload",
                data=data,
                headers=self._headers(session_id),
            )
            return self._raise_for_status(resp)

    async def upload_photo_by_url(
        self,
        session_id: str,
        url: str,
        caption: str,
    ) -> dict[str, Any]:
        """Upload a single photo post from a public URL."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/photo/upload/by/url",
                data={"url": url, "caption": caption},
                headers=self._headers(session_id),
            )
            return self._raise_for_status(resp)

    async def upload_video(
        self,
        session_id: str,
        file_path: str,
        caption: str,
        thumbnail: str | None = None,
        location: str | None = None,
    ) -> dict[str, Any]:
        """Upload a single video post."""
        data: dict[str, Any] = {"file": file_path, "caption": caption}
        if thumbnail:
            data["thumbnail"] = thumbnail
        if location:
            data["location"] = location
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/video/upload",
                data=data,
                headers=self._headers(session_id),
            )
            return self._raise_for_status(resp)

    async def upload_video_by_url(
        self,
        session_id: str,
        url: str,
        caption: str,
        thumbnail: str | None = None,
    ) -> dict[str, Any]:
        """Upload a single video post from a public URL."""
        data: dict[str, Any] = {"url": url, "caption": caption}
        if thumbnail:
            data["thumbnail"] = thumbnail
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/video/upload/by/url",
                data=data,
                headers=self._headers(session_id),
            )
            return self._raise_for_status(resp)

    async def upload_album(
        self,
        session_id: str,
        file_paths: list[str],
        caption: str,
        location: str | None = None,
    ) -> dict[str, Any]:
        """Upload a carousel/album post (up to 10 items).

        ``file_paths`` are paths accessible inside the sidecar container.
        Mixed photo/video is supported.
        """
        data: dict[str, Any] = {
            "files": file_paths,
            "caption": caption,
        }
        if location:
            data["location"] = location
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/album/upload",
                data=data,
                headers=self._headers(session_id),
            )
            return self._raise_for_status(resp)

    async def upload_story(
        self,
        session_id: str,
        file_path: str,
        caption: str = "",
        as_video: bool = False,
    ) -> dict[str, Any]:
        """Upload a story post."""
        data: dict[str, Any] = {"file": file_path}
        if caption:
            data["caption"] = caption
        if as_video:
            data["as_video"] = "true"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/story/upload",
                data=data,
                headers=self._headers(session_id),
            )
            return self._raise_for_status(resp)
