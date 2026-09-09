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

import asyncio
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

    # ── Generic browser automation primitives ─────────────────────────────

    async def click(self, selector: str, text: str | None = None) -> dict[str, Any]:
        """Click an element via the bridge."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/session/click",
                json={"selector": selector, "text": text},
            )
            if resp.status_code >= 400:
                raise BrowserBridgeError(resp.status_code, resp.text)
            return resp.json()

    async def fill(self, selector: str, value: str) -> dict[str, Any]:
        """Fill an input/textarea via the bridge."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/session/fill",
                json={"selector": selector, "value": value},
            )
            if resp.status_code >= 400:
                raise BrowserBridgeError(resp.status_code, resp.text)
            return resp.json()

    async def evaluate(self, expression: str) -> dict[str, Any]:
        """Evaluate JS in the browser and return the result."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/session/evaluate",
                json={"expression": expression},
            )
            if resp.status_code >= 400:
                raise BrowserBridgeError(resp.status_code, resp.text)
            return resp.json()

    # ── Threads profile via browser ─────────────────────────────────────

    async def get_threads_profile(self, username: str) -> dict[str, Any]:
        """Read a Threads profile by navigating to the public profile page."""
        # Prefer .com; the canonical URLs are currently www.threads.com
        await self.navigate(f"https://www.threads.com/@{username}")
        await asyncio.sleep(2)
        result = await self.evaluate("""() => {
            const body = document.body.innerText;
            const lines = body.split('\\n');
            const nameEl = document.querySelector('h1, h2');
            const displayName = nameEl ? nameEl.textContent.trim() : '';
            const username = window.location.pathname.replace(/^\\/@/, '');
            // Find the follower line, then walk backwards to find the last
            // occurrence of the handle. Bio is everything between them.
            const followerIdx = lines.findIndex(l => /\\d+ followers?/.test(l));
            let bio = '';
            if (followerIdx > 0) {
                let handleIdx = -1;
                for (let i = followerIdx - 1; i >= 0; i--) {
                    if (lines[i].trim() === username) {
                        handleIdx = i;
                        break;
                    }
                }
                if (handleIdx >= 0 && handleIdx < followerIdx) {
                    const bioLines = [];
                    for (let i = handleIdx + 1; i < followerIdx; i++) {
                        const l = lines[i].trim();
                        if (!l) continue;
                        if (l === displayName) continue;
                        bioLines.push(l);
                    }
                    bio = bioLines.join('\\n');
                }
            }
            return {
                url: document.location.href,
                username: username,
                full_name: displayName,
                biography: bio,
                raw: { sample: body.substring(0, 500) },
            };
        }""")
        return result.get("result", {})

    async def update_threads_profile(
        self,
        username: str,
        biography: str | None = None,
        full_name: str | None = None,
        website: str | None = None,
    ) -> dict[str, Any]:
        """Update Threads bio/name via the logged-in browser session.

        Threads has no official write API for profile fields, so we automate
        the web UI through the VNC browser.
        """
        updated: list[str] = []
        ignored: list[str] = []

        if full_name is not None:
            ignored.append("full_name")  # Not supported yet

        if website is not None:
            ignored.append("website")  # Not supported yet

        if biography is not None:
            try:
                await self.navigate(f"https://www.threads.com/@{username}")
                await asyncio.sleep(2)
                await self.click('div[role="button"]', "Edit profile")
                await asyncio.sleep(2)
                await self.click('div[role="button"]', "Bio")
                await asyncio.sleep(2)
                await self.fill("textarea", biography)
                await asyncio.sleep(1)
                # Click the *last* Done in the DOM (the one inside the modal)
                await self.evaluate("""(function() {
                    const all = Array.from(document.querySelectorAll('div[role="button"]'));
                    const done = all.filter(b => b.innerText.trim() === 'Done').pop();
                    if (done) { done.click(); return 'clicked'; }
                    return 'no Done';
                })()""")
                await asyncio.sleep(3)
                updated.append("biography")
            except Exception as e:
                raise BrowserBridgeError(500, f"Threads bio update failed: {e}")

        return {
            "platform": "threads",
            "status": "updated",
            "updated_fields": updated,
            "ignored_fields": ignored,
        }
