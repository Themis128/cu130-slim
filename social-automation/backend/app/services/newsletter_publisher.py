"""Shared LinkedIn newsletter publish path — sidecar primary, bridge fallback.

Extracted from ``app.api.newsletter`` so both the synchronous
``POST /newsletter/linkedin/issue`` endpoint and the queued
``newsletter_issues`` dispatch worker publish through ONE code path.
"""

from __future__ import annotations

import base64
import html as html_mod
import logging
from typing import Any

import httpx

from app.services.browser_bridge import BrowserBridgeError
from app.services.linkedin_newsletter_bridge import (
    LinkedInNewsletterBridge,
    LinkedInNewsletterBridgeError,
)
from app.services.linkedin_sidecar import LinkedInSidecarClient, LinkedInSidecarError

logger = logging.getLogger(__name__)


def text_to_html(text: str) -> str:
    """Wrap plain text paragraphs in <p> tags for the ProseMirror paste path."""
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    return "\n".join(f"<p>{html_mod.escape(p, quote=False).replace(chr(10), '<br>')}</p>" for p in paras)


async def publish_with_fallback(
    *,
    title: str,
    html_body: str | None = None,
    text_body: str | None = None,
    cover_base64: str | None = None,
    cover_filename: str | None = None,
    newsletter_name: str | None = None,
    seo_title: str | None = None,
    seo_description: str | None = None,
    schedule_iso: str | None = None,
    post_description: str | None = None,
    dry_run: bool = False,
    browser: str = "auto",
) -> tuple[dict[str, Any], str, list[str]]:
    """Publish a resolved issue. Returns ``(result, via, warnings)``.

    ``via`` is ``"sidecar"`` or ``"bridge"``. Raises ``LinkedInSidecarError``
    only when ``browser="sidecar"`` and the sidecar fails; otherwise raises
    the last bridge error after both paths fail.
    """
    result: dict[str, Any] | None = None
    sidecar_error: str | None = None
    if browser != "bridge":
        client = LinkedInSidecarClient()
        try:
            result = await client.publish_newsletter_issue(
                title=title,
                html_body=html_body,
                text_body=text_body,
                cover_base64=cover_base64,
                cover_filename=cover_filename,
                newsletter_name=newsletter_name,
                seo_title=seo_title,
                seo_description=seo_description,
                schedule_iso=schedule_iso,
                post_description=post_description,
                dry_run=dry_run,
            )
        except LinkedInSidecarError as exc:
            sidecar_error = exc.detail
        except httpx.HTTPError as exc:
            # Transport failures (ReadTimeout, ConnectError) never become
            # LinkedInSidecarError — the editor boot takes minutes and the
            # 420s publish timeout is the common dead-editor signature.
            sidecar_error = f"{type(exc).__name__}: {exc}"

    if result is None:
        if browser == "sidecar":
            raise LinkedInSidecarError(502, sidecar_error or "sidecar failed")
        bridge_html = html_body or text_to_html(text_body or "")
        try:
            result = await LinkedInNewsletterBridge().publish_issue(
                title=title,
                html_body=bridge_html,
                cover_base64=cover_base64,
                seo_title=seo_title,
                seo_description=seo_description,
                post_description=post_description,
                schedule_iso=schedule_iso,
                dry_run=dry_run,
            )
        except (LinkedInNewsletterBridgeError, BrowserBridgeError, httpx.HTTPError) as exc:
            detail = getattr(exc, "detail", str(exc))
            raise LinkedInNewsletterBridgeError(f"sidecar: {sidecar_error or 'skipped'}; bridge: {detail}") from exc
        warnings = ["linkedin sidecar failed — issue handled via browser-bridge fallback" if sidecar_error else "published via browser bridge (browser=bridge)"]
        return result, "bridge", warnings
    return result, "sidecar", []


async def publish_stored_issue(issue: Any) -> tuple[dict[str, Any], str, list[str]]:
    """Publish a ``NewsletterIssue`` row (queued dispatch path).

    Cover: explicit ``cover_base64`` wins; otherwise the branded cover is
    rendered at dispatch time so stored rows stay small.
    """
    from app.services.newsletter_cover import render_newsletter_cover

    cover_b64 = issue.cover_base64
    if not cover_b64 and getattr(issue, "render_cover", True):
        png = render_newsletter_cover(
            issue.title,
            issue_number=issue.issue_number,
            issue_date=None,
            category=issue.category,
        )
        cover_b64 = base64.b64encode(png).decode()

    return await publish_with_fallback(
        title=issue.title,
        html_body=issue.html_body,
        cover_base64=cover_b64,
        cover_filename="cover.png",
        newsletter_name=issue.newsletter_name,
        seo_title=issue.seo_title or issue.title,
        seo_description=(issue.seo_description or "")[:300] or None,
        schedule_iso=None,
        post_description=issue.post_description,
        dry_run=issue.dry_run,
        browser="auto",
    )
