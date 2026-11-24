"""Shared LinkedIn newsletter publish path — sidecar primary, bridge fallback.

Extracted from ``app.api.newsletter`` so both the synchronous
``POST /newsletter/linkedin/issue`` endpoint and the queued
``newsletter_issues`` dispatch worker publish through ONE code path.
"""

from __future__ import annotations

import asyncio
import base64
import html as html_mod
import logging
from typing import Any
from urllib.parse import urlsplit

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
    media: list[dict[str, Any]] | None = None,
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
                media=media,
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


MEDIA_SPEC_KEY = "_media"


def _media_url_allowed(url: str) -> bool:
    """SSRF guard — ``url`` media items may only point at origins this
    platform itself serves media from (``MEDIA_PUBLIC_BASE_URL`` /
    ``R2_PUBLIC_URL``). Anything else is dropped before it can reach the
    sidecar's server-side fetch."""
    from app.core.config import settings

    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    origin = f"{parts.scheme}://{parts.netloc}".lower()
    for base in (settings.MEDIA_PUBLIC_BASE_URL, settings.R2_PUBLIC_URL):
        base = (base or "").strip()
        if not base:
            continue
        try:
            b = urlsplit(base if "://" in base else f"https://{base}")
        except ValueError:
            continue
        if b.scheme and b.netloc and f"{b.scheme}://{b.netloc}".lower() == origin:
            return True
    return False


async def resolve_media_items(db: Any, media: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Resolve a stored media spec into sidecar-ready items.

    Spec entries: ``{type:"image"|"video", asset_id}`` (bytes are loaded
    from the asset's storage backend and sent as base64 — R2 URLs are
    private), ``{type, base64, filename?}``, or ``{type, url, filename?}``
    (public URL the sidecar can fetch). Bad entries are dropped, never
    fatal — media must not block a publish.
    """
    if not media:
        return []
    from sqlalchemy import select

    from app.models.content import MediaAsset
    from app.services.media_dedup import load_asset_bytes

    async def _load_with_retry(asset: Any) -> bytes:
        # R2/MinIO GETs die on transient transport drops (RemoteProtocolError,
        # ConnectError, timeouts — e.g. a stale pooled connection a middlebox
        # silently closed). One retry lands them; GET is idempotent.
        delay = 0.5
        for attempt in range(3):
            try:
                return await load_asset_bytes(asset)
            except httpx.TransportError:
                if attempt == 2:
                    raise
                await asyncio.sleep(delay)
                delay *= 2
        raise AssertionError("unreachable")

    out: list[dict[str, Any]] = []
    for item in media:
        if not isinstance(item, dict):
            continue
        mtype = "video" if item.get("type") == "video" else "image"
        if item.get("base64") or item.get("url"):
            url = item.get("url")
            # Only the url path reaches the sidecar's server-side fetch —
            # when base64 is present it wins and the url is ignored.
            if url and not item.get("base64") and not _media_url_allowed(str(url)):
                logger.warning("newsletter media url rejected (not an allowed media origin): %s", url)
                continue
            out.append({"type": mtype, "base64": item.get("base64"), "url": item.get("url"), "filename": item.get("filename")})
            continue
        asset_id = item.get("asset_id")
        if asset_id and db is not None:
            try:
                asset = (await db.execute(select(MediaAsset).where(MediaAsset.id == asset_id))).scalar_one_or_none()
                if asset is None:
                    continue
                data = await _load_with_retry(asset)
                out.append({"type": mtype, "base64": base64.b64encode(data).decode(), "filename": item.get("filename") or asset.filename})
            except Exception as exc:  # noqa: BLE001 — skip bad asset
                logger.warning("newsletter media resolve failed for %s: %s", asset_id, exc)
    return out


async def publish_stored_issue(issue: Any, db: Any = None) -> tuple[dict[str, Any], str, list[str]]:
    """Publish a ``NewsletterIssue`` row (queued dispatch path).

    Cover: explicit ``cover_base64`` wins; otherwise the branded cover is
    rendered at dispatch time so stored rows stay small. Inline media spec
    rides in ``result["_media"]`` (merge-preserved across publish results).
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

    media = await resolve_media_items(db, (issue.result or {}).get(MEDIA_SPEC_KEY))

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
        media=media or None,
        dry_run=issue.dry_run,
        browser="auto",
    )
