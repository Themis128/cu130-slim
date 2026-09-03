"""HTTP client for the LinkedIn Browser Automation sidecar.

The sidecar is a Node.js + Playwright container (linkedin-browser-sidecar)
that exposes a REST API on port 9225 for LinkedIn profile management.

This client wraps the sidecar's HTTP endpoints so the SocialAuto backend
can update LinkedIn personal profiles and Company Pages without running
Playwright inside the Python process.
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class LinkedInSidecarError(Exception):
    """Raised when the LinkedIn sidecar returns an error."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"LinkedIn sidecar error {status_code}: {detail}")


class LinkedInSidecarClient:
    """HTTP client for the linkedin-browser-sidecar service."""

    def __init__(self, base_url: str | None = None, timeout: float = 120.0) -> None:
        self.base_url = (
            base_url
            or os.environ.get("LINKEDIN_BROWSER_SIDECAR_URL")
            or "http://linkedin-browser-sidecar:9225"
        )
        self._timeout = timeout

    @property
    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, timeout=self._timeout)

    async def health(self) -> dict[str, Any]:
        async with self._client as c:
            r = await c.get("/health")
            r.raise_for_status()
            return r.json()

    async def set_session(self, storage_state: dict) -> dict[str, Any]:
        async with self._client as c:
            r = await c.post("/session", json={"storage_state": storage_state})
            if r.status_code >= 400:
                raise LinkedInSidecarError(r.status_code, r.text)
            return r.json()

    async def check_session(self) -> dict[str, Any]:
        async with self._client as c:
            r = await c.get("/session")
            if r.status_code >= 400:
                raise LinkedInSidecarError(r.status_code, r.text)
            return r.json()

    # ── Personal profile ──────────────────────────────────────────────────

    async def get_profile(self) -> dict[str, Any]:
        async with self._client as c:
            r = await c.get("/profile")
            if r.status_code >= 400:
                raise LinkedInSidecarError(r.status_code, r.text)
            return r.json()

    async def update_headline(self, headline: str) -> dict[str, Any]:
        async with self._client as c:
            r = await c.post("/profile/headline", json={"headline": headline})
            if r.status_code >= 400:
                raise LinkedInSidecarError(r.status_code, r.text)
            return r.json()

    async def update_about(self, about: str) -> dict[str, Any]:
        async with self._client as c:
            r = await c.post("/profile/about", json={"about": about})
            if r.status_code >= 400:
                raise LinkedInSidecarError(r.status_code, r.text)
            return r.json()

    async def upload_cover(self, image_bytes: bytes, filename: str = "cover.jpg") -> dict[str, Any]:
        b64 = base64.b64encode(image_bytes).decode()
        async with self._client as c:
            r = await c.post("/profile/cover", json={"image_base64": b64, "filename": filename})
            if r.status_code >= 400:
                raise LinkedInSidecarError(r.status_code, r.text)
            return r.json()

    async def upload_picture(self, image_bytes: bytes, filename: str = "profile.jpg") -> dict[str, Any]:
        b64 = base64.b64encode(image_bytes).decode()
        async with self._client as c:
            r = await c.post("/profile/picture", json={"image_base64": b64, "filename": filename})
            if r.status_code >= 400:
                raise LinkedInSidecarError(r.status_code, r.text)
            return r.json()

    async def update_website(self, website: str) -> dict[str, Any]:
        async with self._client as c:
            r = await c.post("/profile/website", json={"website": website})
            if r.status_code >= 400:
                raise LinkedInSidecarError(r.status_code, r.text)
            return r.json()

    async def update_location(self, location: str) -> dict[str, Any]:
        async with self._client as c:
            r = await c.post("/profile/location", json={"location": location})
            if r.status_code >= 400:
                raise LinkedInSidecarError(r.status_code, r.text)
            return r.json()

    async def add_experience(
        self,
        title: str,
        company: str,
        start_date: str | None = None,
        end_date: str | None = None,
        description: str | None = None,
        current: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"title": title, "company": company}
        if start_date:
            payload["start_date"] = start_date
        if end_date:
            payload["end_date"] = end_date
        if description:
            payload["description"] = description
        if current:
            payload["current"] = True
        async with self._client as c:
            r = await c.post("/profile/experience", json=payload)
            if r.status_code >= 400:
                raise LinkedInSidecarError(r.status_code, r.text)
            return r.json()

    async def add_education(
        self,
        school: str,
        degree: str | None = None,
        field_of_study: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"school": school}
        if degree:
            payload["degree"] = degree
        if field_of_study:
            payload["field_of_study"] = field_of_study
        if start_date:
            payload["start_date"] = start_date
        if end_date:
            payload["end_date"] = end_date
        if description:
            payload["description"] = description
        async with self._client as c:
            r = await c.post("/profile/education", json=payload)
            if r.status_code >= 400:
                raise LinkedInSidecarError(r.status_code, r.text)
            return r.json()

    async def add_skill(self, skill: str) -> dict[str, Any]:
        async with self._client as c:
            r = await c.post("/profile/skills", json={"skill": skill})
            if r.status_code >= 400:
                raise LinkedInSidecarError(r.status_code, r.text)
            return r.json()

    # ── Company page ──────────────────────────────────────────────────────

    async def get_company(self, vanity: str) -> dict[str, Any]:
        async with self._client as c:
            r = await c.get(f"/company/{vanity}")
            if r.status_code >= 400:
                raise LinkedInSidecarError(r.status_code, r.text)
            return r.json()

    async def update_company_about(self, vanity: str, about: str) -> dict[str, Any]:
        async with self._client as c:
            r = await c.post(f"/company/{vanity}/about", json={"about": about})
            if r.status_code >= 400:
                raise LinkedInSidecarError(r.status_code, r.text)
            return r.json()

    async def update_company_website(self, vanity: str, website: str) -> dict[str, Any]:
        async with self._client as c:
            r = await c.post(f"/company/{vanity}/website", json={"website": website})
            if r.status_code >= 400:
                raise LinkedInSidecarError(r.status_code, r.text)
            return r.json()

    async def update_company_specialties(self, vanity: str, specialties: list[str]) -> dict[str, Any]:
        async with self._client as c:
            r = await c.post(f"/company/{vanity}/specialties", json={"specialties": specialties})
            if r.status_code >= 400:
                raise LinkedInSidecarError(r.status_code, r.text)
            return r.json()

    async def upload_company_logo(self, vanity: str, image_bytes: bytes, filename: str = "logo.jpg") -> dict[str, Any]:
        b64 = base64.b64encode(image_bytes).decode()
        async with self._client as c:
            r = await c.post(f"/company/{vanity}/logo", json={"image_base64": b64, "filename": filename})
            if r.status_code >= 400:
                raise LinkedInSidecarError(r.status_code, r.text)
            return r.json()

    async def upload_company_cover(self, vanity: str, image_bytes: bytes, filename: str = "cover.jpg") -> dict[str, Any]:
        b64 = base64.b64encode(image_bytes).decode()
        async with self._client as c:
            r = await c.post(f"/company/{vanity}/cover", json={"image_base64": b64, "filename": filename})
            if r.status_code >= 400:
                raise LinkedInSidecarError(r.status_code, r.text)
            return r.json()
