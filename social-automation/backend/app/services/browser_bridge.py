"""HTTP client for the browser-novnc bridge container.

The browser-novnc container runs a headed Chromium with noVNC for visual
login and a FastAPI bridge on port 9223.  This client wraps the bridge
endpoints so the SocialAuto backend can use the logged-in browser session
as a fallback when the aiograpi-rest sidecar fails (challenge_required,
fingerprint mismatch, etc.).

Currently supports Instagram profile read/write via the browser's
authenticated fetch to Instagram's internal web API.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0


class BrowserBridgeError(Exception):
    """Raised when the browser bridge returns an error."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Browser bridge error {status_code}: {detail}")


class BrowserBridgeClient:
    """Thin HTTP wrapper around the browser-novnc bridge API."""

    def __init__(self, base_url: str, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def health(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(f"{self._base_url}/health")
            resp.raise_for_status()
            return resp.json()

    async def start_session(self, platform: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/session/start",
                json={"platform": platform},
            )
            if resp.status_code >= 400:
                raise BrowserBridgeError(resp.status_code, resp.text)
            return resp.json()

    async def session_status(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(f"{self._base_url}/session/status")
            resp.raise_for_status()
            return resp.json()

    async def session_login(self, username: str, password: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/session/login",
                json={"username": username, "password": password},
            )
            if resp.status_code >= 400:
                raise BrowserBridgeError(resp.status_code, resp.text)
            return resp.json()

    async def navigate(self, url: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/session/navigate",
                json={"url": url},
            )
            if resp.status_code >= 400:
                raise BrowserBridgeError(resp.status_code, resp.text)
            return resp.json()

    async def extract_cookies(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(f"{self._base_url}/session/extract")
            if resp.status_code >= 400:
                raise BrowserBridgeError(resp.status_code, resp.text)
            return resp.json()

    async def stop_session(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(f"{self._base_url}/session/stop")
            if resp.status_code >= 400:
                raise BrowserBridgeError(resp.status_code, resp.text)
            return resp.json()

    # ── Instagram profile via browser ────────────────────────────────────

    async def get_instagram_profile(self) -> dict[str, Any]:
        """Read the Instagram profile from the logged-in browser session."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(f"{self._base_url}/profile/instagram")
            if resp.status_code >= 400:
                raise BrowserBridgeError(resp.status_code, resp.text)
            return resp.json()

    async def update_instagram_profile(
        self,
        full_name: str | None = None,
        biography: str | None = None,
        external_url: str | None = None,
    ) -> dict[str, Any]:
        """Update the Instagram profile via the logged-in browser session."""
        payload: dict[str, Any] = {}
        if full_name is not None:
            payload["full_name"] = full_name
        if biography is not None:
            payload["biography"] = biography
        if external_url is not None:
            payload["external_url"] = external_url
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.patch(
                f"{self._base_url}/profile/instagram",
                json=payload,
            )
            if resp.status_code >= 400:
                raise BrowserBridgeError(resp.status_code, resp.text)
            return resp.json()
