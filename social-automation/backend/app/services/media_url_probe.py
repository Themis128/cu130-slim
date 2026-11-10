"""Preflight probe for public media URLs handed to platform crawlers.

Meta's Graph API fetches ``image_url``/``video_url``/carousel children
itself — a URL that 404s, serves HTML, or returns an expired signature
surfaces later as misleading errors like 9004 "Only photo or video can
be accepted as media type". Probing the URL before publish converts
that into an immediate, actionable failure.

Check: GET (streamed — headers only, body never downloaded), follow
redirects, require HTTP 200 and — when the server declares a
content-type — an image/* or video/mp4 payload. Missing content-type is
tolerated (some CDNs omit it); text/html and friends are not.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

_ALLOWED_PREFIXES = ("image/", "video/mp4", "video/quicktime")
_BLOCKED_TYPES = ("text/html", "application/xhtml", "application/json")


async def _probe_one(client: httpx.AsyncClient, url: str, timeout: float) -> dict[str, Any]:
    try:
        async with client.stream("GET", url, timeout=timeout, follow_redirects=True) as resp:
            ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
            ok_status = 200 <= resp.status_code < 300
            if not ok_status:
                return {"url": url, "ok": False, "status": resp.status_code, "content_type": ctype or None,
                        "error": f"HTTP {resp.status_code}"}
            if ctype and ctype in _BLOCKED_TYPES:
                return {"url": url, "ok": False, "status": resp.status_code, "content_type": ctype,
                        "error": f"served {ctype} instead of media"}
            if ctype and not ctype.startswith(_ALLOWED_PREFIXES):
                return {"url": url, "ok": False, "status": resp.status_code, "content_type": ctype,
                        "error": f"unexpected content-type {ctype}"}
            return {"url": url, "ok": True, "status": resp.status_code, "content_type": ctype or None}
    except Exception as exc:  # noqa: BLE001
        return {"url": url, "ok": False, "status": None, "content_type": None,
                "error": f"{type(exc).__name__}: {exc}"[:200]}


async def probe_public_media(urls: list[str], *, timeout: float = 10.0) -> list[dict[str, Any]]:
    """Probe each URL concurrently; returns one result dict per URL."""
    if not urls:
        return []
    async with httpx.AsyncClient(headers={"User-Agent": "SocialAuto-media-preflight/1.0"}) as client:
        return list(await asyncio.gather(*(_probe_one(client, u, timeout) for u in urls)))


def first_failure(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((r for r in results if not r.get("ok")), None)
