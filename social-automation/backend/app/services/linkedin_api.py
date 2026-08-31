"""Standalone LinkedIn REST API client.

This module wraps the LinkedIn API calls needed for the Cloudless social stack
without depending on the database or Celery worker. It is consumed by:

- ``app/api/linkedin.py`` for the LinkedIn + AI endpoints
- ``app/services/publishing.py`` / analytics sync
- One-off scripts and n8n webhooks that need direct LinkedIn API access
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
from typing import Any
from urllib.parse import quote, unquote

import httpx

from app.core.config import get_settings

LINKEDIN_VERSION = "202608"
LINKEDIN_REST_BASE = "https://api.linkedin.com/rest"
LINKEDIN_V2_BASE = "https://api.linkedin.com/v2"
MAX_COMMENTARY_CHARS = 3000

logger = logging.getLogger(__name__)

# LinkedIn URNs look like: urn:li:ugcPost:123456, urn:li:share:abc, urn:li:organization:789
_URN_RE = re.compile(r"^urn:li:[a-zA-Z]+:[a-zA-Z0-9_\-.]+$")


def _validate_urn(urn: str) -> str:
    """Validate a LinkedIn URN to prevent SSRF via crafted identifiers."""
    urn = urn.strip()
    if not urn:
        raise ValueError("URN is empty")
    if not _URN_RE.match(urn):
        raise ValueError(f"Invalid LinkedIn URN format: {urn[:80]}")
    return urn


def _sanitize_log_text(text: str, max_len: int = 400) -> str:
    """Sanitize API response text for safe logging — strips newlines/control chars."""
    # Replace newlines and carriage returns to prevent log injection
    cleaned = text.replace("\n", "\\n").replace("\r", "\\r")
    # Strip other control characters (except tab)
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", cleaned)
    return cleaned[:max_len]


class LinkedInAPIError(Exception):
    """Raised when a LinkedIn API call fails with a non-success status.

    LinkedIn 5xx responses are surfaced as 502/503 gateway errors so the
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
            message = f"LinkedIn API error {status_code} for {url}: {response_text[:400]}"
        super().__init__(message)


@dataclasses.dataclass
class LinkedInPostResult:
    success: bool
    platform_post_id: str | None = None
    platform_url: str | None = None
    error: str | None = None


@dataclasses.dataclass
class LinkedInOrganization:
    urn: str
    id: str
    name: str
    vanity_name: str | None = None
    role: str | None = None


class LinkedInAPIClient:
    """Async LinkedIn API client for a single access token.

    The client does not store decrypted tokens beyond the lifetime of the
    instance. Callers are responsible for encrypting tokens at rest.
    """

    def __init__(self, access_token: str | None = None):
        self.access_token = access_token
        self._settings = get_settings()

    def _headers(self) -> dict[str, str]:
        if not self.access_token:
            raise ValueError("LinkedIn access token is required")
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
            "Linkedin-Version": LINKEDIN_VERSION,
        }

    def _author_urn(self, account_id: str, account_type: str = "person") -> str:
        account_type = (account_type or "person").lower()
        if account_type in ("organization", "company", "page"):
            return f"urn:li:organization:{account_id}"
        return f"urn:li:person:{account_id}"

    def _map_status_code(self, status_code: int) -> int:
        """Normalize upstream 5xx status codes to 502/503 for the FastAPI layer."""
        if status_code >= 500:
            return 503 if status_code in (503, 504) else 502
        return status_code

    def _raise_for_status(self, resp: httpx.Response, url: str) -> None:
        """Raise ``LinkedInAPIError`` for any non-2xx response."""
        if resp.status_code < 400:
            return
        status_code = self._map_status_code(resp.status_code)
        text = _sanitize_log_text(resp.text)
        logger.error("LinkedIn API error %s for %s: %s", status_code, url, text)
        raise LinkedInAPIError(status_code, text, url)

    def _log_api_error(self, url: str, resp: httpx.Response) -> None:
        """Log the response body for a failed LinkedIn API call."""
        if resp.status_code >= 400:
            logger.error(
                "LinkedIn API call to %s failed: HTTP %s: %s",
                url,
                resp.status_code,
                _sanitize_log_text(resp.text),
            )

    def _post_payload(
        self,
        author_urn: str,
        commentary: str,
        visibility: str = "PUBLIC",
        is_reshare_disabled: bool = False,
        content: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "author": author_urn,
            "commentary": commentary[:MAX_COMMENTARY_CHARS],
            "visibility": visibility,
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": is_reshare_disabled,
        }
        if content:
            payload["content"] = content
        return payload

    async def validate_token(self) -> dict[str, Any]:
        """Validate the access token and return basic OpenID profile info.

        Raises ``LinkedInAPIError`` on invalid, expired, or upstream error.
        """
        url = f"{LINKEDIN_V2_BASE}/userinfo"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                url,
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Accept": "application/json",
                },
            )
            self._raise_for_status(resp, url)
            return resp.json()

    async def get_member_organizations(self) -> list[LinkedInOrganization]:
        """Discover Company Pages the authenticated member can administer.

        Tries the REST ``organizationAcls`` endpoint and falls back to the v2
        endpoint if LinkedIn returns a route error.
        """
        urls = [
            f"{LINKEDIN_REST_BASE}/organizationAcls?q=roleAssignee&role=ADMINISTRATOR&state=APPROVED",
            f"{LINKEDIN_REST_BASE}/organizationAcls?q=roleAssignee&state=APPROVED",
            f"{LINKEDIN_V2_BASE}/organizationAcls?q=roleAssignee&role=ADMINISTRATOR&state=APPROVED",
        ]

        elements: list[dict] = []
        async with httpx.AsyncClient(timeout=30.0) as client:
            for url in urls:
                resp = await client.get(url, headers=self._headers())
                if resp.status_code == 200:
                    elements = (resp.json() or {}).get("elements") or []
                    if elements:
                        break

        organizations: list[LinkedInOrganization] = []
        async with httpx.AsyncClient(timeout=30.0) as client:
            for el in elements:
                org_urn = el.get("organization") or el.get("organizationalTarget") or ""
                if not isinstance(org_urn, str) or "organization:" not in org_urn:
                    continue
                org_id = org_urn.rsplit(":", 1)[-1]
                if not org_id:
                    continue

                name = f"LinkedIn Page {org_id}"
                vanity: str | None = None
                for org_url in (
                    f"{LINKEDIN_REST_BASE}/organizations/{org_id}",
                    f"{LINKEDIN_V2_BASE}/organizations/{org_id}",
                ):
                    org_resp = await client.get(org_url, headers=self._headers())
                    if org_resp.status_code != 200:
                        continue
                    org = org_resp.json() or {}
                    name_field = org.get("localizedName") or org.get("name")
                    if isinstance(name_field, str) and name_field.strip():
                        name = name_field.strip()
                    elif isinstance(name_field, dict):
                        localized = name_field.get("localized") if isinstance(name_field.get("localized"), dict) else name_field
                        if isinstance(localized, dict) and localized:
                            name = str(localized.get("en_US") or next(iter(localized.values())))
                    vanity = org.get("vanityName") or vanity
                    break

                organizations.append(
                    LinkedInOrganization(
                        urn=org_urn,
                        id=org_id,
                        name=name,
                        vanity_name=vanity,
                        role=el.get("role"),
                    )
                )

        return organizations

    async def get_organization(self, org_id: str) -> dict[str, Any]:
        """Fetch a single organization by numeric id."""
        url = f"{LINKEDIN_REST_BASE}/organizations/{org_id}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=self._headers())
            self._raise_for_status(resp, url)
            return resp.json()

    async def get_post_analytics(
        self,
        post_urn: str,
        org_urn: str,
    ) -> dict[str, Any]:
        """Return lifetime share statistics for one post belonging to an organization.

        LinkedIn uses ``organizationalEntityShareStatistics`` with either
        ``shares`` or ``ugcPosts`` as the query parameter. This method tries both
        and falls back to the alternate URN form (share <-> ugcPost).
        """
        post_urn = unquote(post_urn.strip())
        org_urn = unquote(org_urn.strip())

        def _urn_kind(urn: str) -> str | None:
            if "ugcPost" in urn:
                return "ugcPosts"
            if ":share:" in urn or urn.startswith("urn:li:share:"):
                return "shares"
            return None

        def _alt_urn(urn: str) -> str | None:
            if "ugcPost" in urn:
                return f"urn:li:share:{urn.rsplit(':', 1)[-1]}"
            if ":share:" in urn:
                return f"urn:li:ugcPost:{urn.rsplit(':', 1)[-1]}"
            return None

        attempts: list[tuple[str, str]] = []
        kind = _urn_kind(post_urn)
        if kind:
            attempts.append((kind, post_urn))
        else:
            attempts.append(("ugcPosts", post_urn))
            attempts.append(("shares", post_urn))
        alt = _alt_urn(post_urn)
        if alt:
            alt_kind = _urn_kind(alt)
            if alt_kind:
                attempts.append((alt_kind, alt))

        base = f"{LINKEDIN_REST_BASE}/organizationalEntityShareStatistics"
        org_q = quote(org_urn, safe="")

        async with httpx.AsyncClient(timeout=60.0) as client:
            for param_name, urn in attempts:
                encoded_list = "List(" + ",".join(quote(urn, safe="") for _ in [urn]) + ")"
                url = f"{base}?q=organizationalEntity&organizationalEntity={org_q}&{param_name}={encoded_list}"
                resp = await client.get(url, headers=self._headers())
                if resp.status_code >= 500:
                    self._raise_for_status(resp, url)
                if resp.status_code >= 400:
                    continue
                data = resp.json() or {}
                elements = data.get("elements") or []
                if elements:
                    return elements[0]

        return {}

    async def get_organization_lifetime_stats(self, org_urn: str) -> dict[str, Any]:
        """Return aggregated lifetime statistics for the whole organization."""
        org_urn = unquote(org_urn.strip())
        org_q = quote(org_urn, safe="")
        url = f"{LINKEDIN_REST_BASE}/organizationalEntityShareStatistics?q=organizationalEntity&organizationalEntity={org_q}"

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(url, headers=self._headers())
            self._raise_for_status(resp, url)
            data = resp.json() or {}
            elements = data.get("elements") or []
            return elements[0] if elements else {}

    async def get_follower_count(self, org_urn: str) -> int:
        """Fetch the current follower count for a Company Page.

        Uses ``networkSizes`` with ``edgeType=CompanyFollowedByMember``. If the
        REST endpoint is unavailable, the method returns ``0`` and logs the
        status code rather than raising.
        """
        org_urn = unquote(org_urn.strip())
        encoded = quote(org_urn, safe="")
        urls = [
            f"{LINKEDIN_REST_BASE}/networkSizes/{encoded}?edgeType=CompanyFollowedByMember&start=0&count=1",
            f"{LINKEDIN_V2_BASE}/networkSizes/{encoded}?edgeType=CompanyFollowedByMember&start=0&count=1",
        ]

        async with httpx.AsyncClient(timeout=30.0) as client:
            for url in urls:
                resp = await client.get(url, headers=self._headers())
                if resp.status_code == 200:
                    data = resp.json() or {}
                    if isinstance(data, dict):
                        first = data.get("first") or {}
                        return int(first.get("totalSize") or data.get("totalSize") or 0)
                    if isinstance(data, list) and data:
                        return int(data[0].get("totalSize") or 0)
                if resp.status_code in (403, 404):
                    # Missing product access / route not found — try v2 next.
                    continue
                break

        return 0

    async def create_post(
        self,
        author_urn: str,
        commentary: str,
        *,
        visibility: str = "PUBLIC",
        is_reshare_disabled: bool = False,
        link_url: str | None = None,
        link_title: str | None = None,
        link_description: str | None = None,
    ) -> LinkedInPostResult:
        """Publish a text or link-preview post.

        For image or document posts use ``create_multi_image_post`` or
        ``create_document_post``.
        """
        if not author_urn.startswith("urn:li:"):
            return LinkedInPostResult(success=False, error=f"Invalid author URN: {author_urn}")

        payload = self._post_payload(
            author_urn=author_urn,
            commentary=commentary,
            visibility=visibility,
            is_reshare_disabled=is_reshare_disabled,
        )

        if link_url:
            payload["content"] = {
                "article": {
                    "source": link_url,
                    "title": link_title or "",
                    "description": link_description or "",
                }
            }

        url = f"{LINKEDIN_REST_BASE}/posts"
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, headers=self._headers(), json=payload)
            if resp.status_code >= 400:
                self._log_api_error(url, resp)
                return LinkedInPostResult(
                    success=False,
                    error=f"HTTP {resp.status_code}: {resp.text[:400]}",
                )
            post_id = unquote(resp.headers.get("x-restli-id", "") or "")
            return LinkedInPostResult(
                success=True,
                platform_post_id=post_id,
                platform_url=f"https://www.linkedin.com/feed/update/{post_id}" if post_id else None,
            )

    async def create_multi_image_post(
        self,
        author_urn: str,
        commentary: str,
        media_paths: list[str],
    ) -> LinkedInPostResult:
        """Publish a single-image or multi-image (carousel) LinkedIn post."""
        if not author_urn.startswith("urn:li:"):
            return LinkedInPostResult(success=False, error=f"Invalid author URN: {author_urn}")

        init_url = f"{LINKEDIN_REST_BASE}/images?action=initializeUpload"
        image_urns: list[str] = []

        async with httpx.AsyncClient(timeout=120.0) as client:
            for path in media_paths[:20]:
                try:
                    with open(path, "rb") as fh:
                        img_bytes = fh.read()
                except OSError as exc:
                    return LinkedInPostResult(success=False, error=f"Could not read image {path}: {exc}")

                init = await client.post(
                    init_url,
                    headers=self._headers(),
                    json={"initializeUploadRequest": {"owner": author_urn}},
                )
                if init.status_code >= 400:
                    self._log_api_error(init_url, init)
                    return LinkedInPostResult(
                        success=False,
                        error=f"HTTP {init.status_code}: {init.text[:400]}",
                    )

                value = (init.json() or {}).get("value") or {}
                upload_url = value.get("uploadUrl")
                image_urn = value.get("image")
                if not upload_url or not image_urn:
                    return LinkedInPostResult(
                        success=False,
                        error="LinkedIn image upload initialization returned no upload details",
                    )

                up = await client.put(
                    upload_url,
                    headers={"Authorization": f"Bearer {self.access_token}"},
                    content=img_bytes,
                )
                if up.status_code >= 400:
                    self._log_api_error(upload_url, up)
                    return LinkedInPostResult(
                        success=False,
                        error=f"Image upload failed: HTTP {up.status_code}: {up.text[:400]}",
                    )

                encoded = quote(image_urn, safe="")
                poll_url = f"{LINKEDIN_REST_BASE}/images/{encoded}"
                status = await self._poll_asset_status(client, poll_url, self._headers())
                if status != "AVAILABLE":
                    return LinkedInPostResult(
                        success=False,
                        error=f"Image processing failed or timed out (status={status})",
                    )

                image_urns.append(image_urn)

            if len(image_urns) >= 2:
                content: dict[str, Any] = {
                    "multiImage": {
                        "images": [
                            {"id": urn, "altText": f"Slide {i + 1}"}
                            for i, urn in enumerate(image_urns)
                        ]
                    }
                }
            elif image_urns:
                content = {"media": {"id": image_urns[0], "title": "Image"}}
            else:
                return LinkedInPostResult(success=False, error="No images were successfully uploaded")

            payload = self._post_payload(author_urn=author_urn, commentary=commentary, content=content)
            post_url = f"{LINKEDIN_REST_BASE}/posts"
            pr = await client.post(post_url, headers=self._headers(), json=payload)
            if pr.status_code >= 400:
                self._log_api_error(post_url, pr)
                return LinkedInPostResult(
                    success=False,
                    error=f"HTTP {pr.status_code}: {pr.text[:400]}",
                )
            post_id = unquote(pr.headers.get("x-restli-id", "") or "")
            return LinkedInPostResult(
                success=True,
                platform_post_id=post_id,
                platform_url=f"https://www.linkedin.com/feed/update/{post_id}" if post_id else None,
            )

    async def create_document_post(
        self,
        author_urn: str,
        commentary: str,
        pdf_bytes: bytes,
        *,
        title: str = "Document",
    ) -> LinkedInPostResult:
        """Publish a LinkedIn post with a PDF document (carousel)."""
        if not author_urn.startswith("urn:li:"):
            return LinkedInPostResult(success=False, error=f"Invalid author URN: {author_urn}")

        init_url = f"{LINKEDIN_REST_BASE}/documents?action=initializeUpload"
        async with httpx.AsyncClient(timeout=120.0) as client:
            reg = await client.post(
                init_url,
                headers=self._headers(),
                json={"initializeUploadRequest": {"owner": author_urn}},
            )
            if reg.status_code >= 400:
                self._log_api_error(init_url, reg)
                return LinkedInPostResult(
                    success=False,
                    error=f"HTTP {reg.status_code}: {reg.text[:400]}",
                )

            value = (reg.json() or {}).get("value") or {}
            upload_url = value.get("uploadUrl")
            document_urn = value.get("document")
            if not upload_url or not document_urn:
                return LinkedInPostResult(
                    success=False,
                    error="LinkedIn document upload initialization returned no upload details",
                )

            up = await client.put(
                upload_url,
                headers={"Authorization": f"Bearer {self.access_token}"},
                content=pdf_bytes,
            )
            if up.status_code >= 400:
                self._log_api_error(upload_url, up)
                return LinkedInPostResult(
                    success=False,
                    error=f"Document upload failed: HTTP {up.status_code}: {up.text[:400]}",
                )

            encoded = quote(document_urn, safe="")
            poll_url = f"{LINKEDIN_REST_BASE}/documents/{encoded}"
            status = await self._poll_asset_status(
                client,
                poll_url,
                self._headers(),
                max_attempts=30,
                interval=2.0,
            )
            if status != "AVAILABLE":
                return LinkedInPostResult(
                    success=False,
                    error=f"Document processing failed or timed out (status={status})",
                )

            content = {
                "media": {
                    "title": title[:200],
                    "id": document_urn,
                }
            }
            payload = self._post_payload(
                author_urn=author_urn,
                commentary=commentary,
                content=content,
            )
            post_url = f"{LINKEDIN_REST_BASE}/posts"
            pr = await client.post(post_url, headers=self._headers(), json=payload)
            if pr.status_code >= 400:
                self._log_api_error(post_url, pr)
                return LinkedInPostResult(
                    success=False,
                    error=f"HTTP {pr.status_code}: {pr.text[:400]}",
                )
            post_id = unquote(pr.headers.get("x-restli-id", "") or "")
            return LinkedInPostResult(
                success=True,
                platform_post_id=post_id,
                platform_url=f"https://www.linkedin.com/feed/update/{post_id}" if post_id else None,
            )

    async def _poll_asset_status(
        self,
        client: httpx.AsyncClient,
        url: str,
        headers: dict[str, str],
        *,
        max_attempts: int = 20,
        interval: float = 1.0,
    ) -> str | None:
        """Poll a LinkedIn asset until it becomes ``AVAILABLE``.

        Returns the last status seen (``AVAILABLE``, ``PROCESSING_FAILED``,
        or ``None`` on timeout).
        """
        last_status: str | None = None
        for _ in range(max_attempts):
            await asyncio.sleep(interval)
            st = await client.get(url, headers=headers)
            if st.status_code >= 400:
                # The asset may not be queryable yet; keep polling.
                continue
            data = st.json() or {}
            last_status = data.get("status")
            if last_status == "AVAILABLE":
                return "AVAILABLE"
            if last_status == "PROCESSING_FAILED":
                return "PROCESSING_FAILED"
        return last_status

    async def create_comment(
        self,
        post_urn: str,
        text: str,
        *,
        creator_urn: str,
    ) -> LinkedInPostResult:
        """Add a comment to an existing LinkedIn post.

        The caller must provide a ``creator_urn`` (person or organization) with
        permission to comment on the target post.
        """
        post_urn = unquote(post_urn.strip())
        post_urn = _validate_urn(post_urn)
        encoded = quote(post_urn, safe="")
        url = f"{LINKEDIN_REST_BASE}/socialActions/{encoded}/comments"

        payload = {
            "actor": creator_urn,
            "message": {"text": text[:MAX_COMMENTARY_CHARS]},
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, headers=self._headers(), json=payload)
            if resp.status_code >= 400:
                self._log_api_error(url, resp)
                return LinkedInPostResult(
                    success=False,
                    error=f"HTTP {resp.status_code}: {resp.text[:400]}",
                )
            comment_id = unquote(resp.headers.get("x-restli-id", "") or "")
            return LinkedInPostResult(
                success=True,
                platform_post_id=comment_id,
                platform_url=f"https://www.linkedin.com/feed/update/{post_urn}?commentId={comment_id}" if comment_id else None,
            )

    async def create_article(
        self,
        author_urn: str,
        title: str,
        body: str,
        *,
        link_url: str | None = None,
    ) -> LinkedInPostResult:
        """Publish a long-form post that reads like a LinkedIn article.

        LinkedIn does not expose a native Article creation API for general apps.
        This method posts the content as a long ``commentary`` with an optional
        link preview. If the body exceeds 3000 characters it is truncated.
        """
        header = f"{title}\n\n" if title else ""
        commentary = (header + body)[:MAX_COMMENTARY_CHARS]
        return await self.create_post(
            author_urn=author_urn,
            commentary=commentary,
            link_url=link_url,
            link_title=title,
            link_description=None,
        )

    async def delete_post(self, post_urn: str) -> LinkedInPostResult:
        """Delete a LinkedIn post by URN."""
        post_urn = unquote(post_urn.strip())
        post_urn = _validate_urn(post_urn)
        encoded = quote(post_urn, safe="")
        url = f"{LINKEDIN_REST_BASE}/posts/{encoded}"

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.delete(url, headers=self._headers())
            if resp.status_code >= 400:
                self._log_api_error(url, resp)
                return LinkedInPostResult(
                    success=False,
                    error=f"HTTP {resp.status_code}: {resp.text[:400]}",
                )
            return LinkedInPostResult(success=True)
