"""HTTP client for the Facebook Browser Automation sidecar.

The sidecar is a Node.js + Playwright container (facebook-browser-sidecar)
that exposes a REST API on port 9226 for Facebook personal-profile operations
that the official Graph API does not support:

- Personal-profile posting (text, photo, link, video)
- Bio / intro text edits
- Profile picture and cover photo uploads
- Website in contact info
- Page-mode posting (list pages, switch, post as page)

This client wraps the sidecar's HTTP endpoints so the SocialAuto backend can
drive Facebook browser automation without running Playwright inside the
Python process.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# Short-TTL cache for get_profile_stats — one sync cycle hits it twice
# (sync_facebook_account + the follower-snapshot helper) and a fresh
# browser navigation each time is ~30s of page loads.
_PROFILE_STATS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


class FacebookSidecarError(Exception):
    """Raised when the Facebook sidecar returns an error."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Facebook sidecar error {status_code}: {detail}")


class FacebookSidecarClient:
    """HTTP client for the facebook-browser-sidecar service."""

    def __init__(self, base_url: str | None = None, timeout: float = 180.0) -> None:
        self.base_url = (
            base_url
            or os.environ.get("FACEBOOK_BROWSER_SIDECAR_URL")
            or "http://facebook-browser-sidecar:9226"
        )
        self._timeout = timeout

    @property
    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, timeout=self._timeout)

    async def _post(
        self, path: str, json: dict[str, Any], timeout: float | None = None
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url=self.base_url, timeout=timeout or self._timeout
        ) as c:
            r = await c.post(path, json=json)
            if r.status_code >= 400:
                raise FacebookSidecarError(r.status_code, r.text)
            return r.json()

    async def _get(self, path: str) -> dict[str, Any]:
        async with self._client as c:
            r = await c.get(path)
            if r.status_code >= 400:
                raise FacebookSidecarError(r.status_code, r.text)
            return r.json()

    # ── Health & session ──────────────────────────────────────────────────

    async def health(self) -> dict[str, Any]:
        return await self._get("/health")

    async def set_session(self, storage_state: dict) -> dict[str, Any]:
        return await self._post("/session", {"storage_state": storage_state})

    async def check_session(self) -> dict[str, Any]:
        return await self._get("/session")

    async def login(self, username: str, password: str, verification_code: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"username": username, "password": password}
        if verification_code:
            payload["verification_code"] = verification_code
        return await self._post("/login", payload)

    # ── Personal profile ──────────────────────────────────────────────────

    async def get_profile(self) -> dict[str, Any]:
        return await self._get("/profile")

    async def get_profile_stats(
        self, expected_name: str | None = None
    ) -> dict[str, Any]:
        """Scrape follower count + professional-dashboard stats for the
        logged-in personal profile.

        The Graph API exposes none of this for personal profiles —
        ``followers_count`` and post insights only exist for Pages — so
        pro-mode profile analytics come from the rendered UI: the profile
        page ("N followers") and professional_dashboard (Views / Engagement /
        Net follows over the trailing 28 days).

        The sidecar session is shared — ``expected_name`` guards against
        attributing stats to the wrong SocialAccount when a different
        profile holds the session. Results are cached 5 min so the
        follower-snapshot helper and the sync pass share one scrape.
        """
        cache_key = expected_name or ""
        cached = _PROFILE_STATS_CACHE.get(cache_key)
        if cached and cached[0] > time.time():
            return dict(cached[1])

        nav = await self._post(
            "/debug/navigate", {"url": "https://www.facebook.com/me"}
        )
        # Poll until the profile header mounts ("N followers") — the SPA
        # needs a few seconds on a cold navigate.
        prof_text = ""
        for _ in range(6):
            await asyncio.sleep(3)
            prof = await self._post(
                "/debug/eval",
                {"script": "document.body.innerText.substring(0,6000)"},
            )
            prof_text = str(prof.get("result") or "")
            if "followers" in prof_text:
                break
        followers = None
        m = re.search(r"([\d,]+)\s+followers", prof_text)
        if m:
            followers = int(m.group(1).replace(",", ""))

        # Display name sits on the line before "N followers • M following".
        profile_name = None
        prof_lines = [
            ln.strip() for ln in prof_text.splitlines() if ln.strip()
        ]
        for i, ln in enumerate(prof_lines):
            if "followers" in ln and i > 0:
                profile_name = prof_lines[i - 1]
                break
        if expected_name and (
            not profile_name
            or expected_name.strip().casefold() != profile_name.casefold()
        ):
            raise FacebookSidecarError(
                409,
                f"profile mismatch: sidecar session is "
                f"{profile_name!r}, expected {expected_name!r} — "
                "refusing to attribute another profile's stats",
            )

        await self._post(
            "/debug/navigate",
            {"url": "https://www.facebook.com/professional_dashboard/"},
        )
        # The dashboard is a lazy-rendered SPA — poll until the Insights
        # cards mount ("Net follows" label) instead of a fixed sleep.
        dash_text = ""
        for _ in range(10):
            await asyncio.sleep(3)
            dash = await self._post(
                "/debug/eval",
                {"script": "document.body.innerText.substring(0,8000)"},
            )
            dash_text = str(dash.get("result") or "")
            if "Net follows" in dash_text:
                break
        # Dashboard cards render as: value line, % change line, label line
        # ("209", "895%", "Views"). Value is the numeric line two rows above
        # the label.
        stats: dict[str, Any] = {
            "followers": followers,
            "profile_name": profile_name,
            "profile_url": nav.get("url"),
        }
        lines = [
            ln.strip().replace("﻿", "").replace("​", "")
            for ln in dash_text.splitlines()
            if ln.strip()
        ]  # dashboard % lines carry a leading zero-width char (﻿895%)
        for label, key in (
            ("Views", "views_28d"),
            ("Engagement", "engagement_28d"),
            ("Net follows", "net_follows_28d"),
        ):
            # The label also appears in the nav sidebar — a stat card is the
            # occurrence whose preceding line is a % change figure.
            for i, ln in enumerate(lines):
                if ln != label:
                    continue
                if i == 0 or not re.fullmatch(r"[\d.]+%", lines[i - 1]):
                    continue
                for cand in reversed(lines[max(0, i - 3) : i - 1]):
                    if re.fullmatch(r"[\d,]+", cand):
                        stats[key] = int(cand.replace(",", ""))
                        break
                break
        _PROFILE_STATS_CACHE[cache_key] = (time.time() + 300, dict(stats))
        return stats

    async def update_bio(self, bio: str) -> dict[str, Any]:
        return await self._post("/profile/bio", {"bio": bio})

    async def upload_picture(self, image_bytes: bytes, filename: str = "profile.jpg") -> dict[str, Any]:
        b64 = base64.b64encode(image_bytes).decode()
        return await self._post("/profile/picture", {"image_base64": b64, "filename": filename})

    async def upload_cover(self, image_bytes: bytes, filename: str = "cover.jpg") -> dict[str, Any]:
        b64 = base64.b64encode(image_bytes).decode()
        return await self._post("/profile/cover", {"image_base64": b64, "filename": filename})

    async def update_website(self, website: str) -> dict[str, Any]:
        return await self._post("/profile/website", {"website": website})

    async def update_work(
        self,
        company: str,
        position: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"company": company}
        if position:
            payload["position"] = position
        if description:
            payload["description"] = description
        return await self._post("/profile/work", payload)

    async def update_education(
        self,
        school: str,
        degree: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"school": school}
        if degree:
            payload["degree"] = degree
        return await self._post("/profile/education", payload)

    async def update_location(
        self,
        current_city: str | None = None,
        hometown: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if current_city:
            payload["current_city"] = current_city
        if hometown:
            payload["hometown"] = hometown
        return await self._post("/profile/location", payload)

    async def update_quotes(self, quotes: str) -> dict[str, Any]:
        return await self._post("/profile/quotes", {"quotes": quotes})

    async def update_contact(
        self,
        email: str | None = None,
        phone: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if email:
            payload["email"] = email
        if phone:
            payload["phone"] = phone
        return await self._post("/profile/contact", payload)

    async def export_cookies(self) -> dict[str, Any]:
        return await self._get("/profile/cookies")

    # ── Personal posting ──────────────────────────────────────────────────

    async def post_text(self, message: str, privacy: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"message": message}
        if privacy:
            payload["privacy"] = privacy
        return await self._post("/post/text", payload)

    async def post_photo(
        self,
        images: list[dict[str, str]],
        message: str | None = None,
        privacy: str | None = None,
    ) -> dict[str, Any]:
        """Post photo(s) to the personal profile.

        Args:
            images: list of dicts with ``image_base64`` and optional ``filename``.
            message: optional caption.
            privacy: 'public', 'friends', or 'only_me'.
        """
        payload: dict[str, Any] = {"images": images}
        if message:
            payload["message"] = message
        if privacy:
            payload["privacy"] = privacy
        return await self._post("/post/photo", payload)

    async def post_link(self, url: str, message: str | None = None, privacy: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"url": url}
        if message:
            payload["message"] = message
        if privacy:
            payload["privacy"] = privacy
        return await self._post("/post/link", payload)

    async def post_video(
        self,
        video_bytes: bytes,
        filename: str = "video.mp4",
        message: str | None = None,
        privacy: str | None = None,
    ) -> dict[str, Any]:
        b64 = base64.b64encode(video_bytes).decode()
        payload: dict[str, Any] = {"video_base64": b64, "filename": filename}
        if message:
            payload["message"] = message
        if privacy:
            payload["privacy"] = privacy
        # Video processing + post-publish verification can exceed the
        # default 180s on the sidecar (settle up to 120s + verify ~60s).
        return await self._post("/post/video", payload, timeout=300.0)

    # ── Page mode ─────────────────────────────────────────────────────────

    async def list_pages(self) -> dict[str, Any]:
        return await self._get("/pages")

    async def use_page(self, page_id: str) -> dict[str, Any]:
        return await self._post(f"/page/{page_id}/use", {})

    async def page_post_text(self, message: str) -> dict[str, Any]:
        return await self._post("/page/post/text", {"message": message})

    async def page_post_photo(
        self,
        images: list[dict[str, str]],
        message: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"images": images}
        if message:
            payload["message"] = message
        return await self._post("/page/post/photo", payload)
