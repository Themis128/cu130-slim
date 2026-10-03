"""Fetch Open Graph / Twitter Card metadata for a URL.

Used by the Edit Post UI to show how ``link_url`` will render when the
platform builds its link card — and to populate ``link_preview_override``.

Self-hosted, no external API: a direct httpx fetch + meta-tag parse.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

_MAX_BYTES = 512 * 1024  # enough for the <head> of any sane page
_TIMEOUT = 8.0
_UA = "SocialAuto-LinkPreview/1.0 (+https://cloudless.gr)"


def _resolve_public_host(host: str) -> None:
    """Reject hosts that resolve to non-public IPs (SSRF guard)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ValueError("Cannot resolve host") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            raise ValueError("URL host is not publicly routable")


def _meta(soup: BeautifulSoup, *names: str) -> str | None:
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        content = str(tag.get("content", "")).strip() if tag else ""
        if content:
            return content
    return None


async def fetch_link_preview(url: str) -> dict:
    """Return ``{url, title, description, image, site_name}`` for ``url``.

    Raises ``ValueError`` for invalid/non-public URLs and ``httpx.HTTPError``
    for fetch failures.
    """
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("URL must be http(s) with a host")
    _resolve_public_host(parsed.hostname)

    async with httpx.AsyncClient(
        timeout=_TIMEOUT,
        follow_redirects=True,
        max_redirects=5,
        headers={"User-Agent": _UA, "Accept": "text/html,*/*;q=0.8"},
    ) as client:
        resp = await client.get(url.strip())
        resp.raise_for_status()
        body = resp.content[:_MAX_BYTES]

    soup = BeautifulSoup(body, "lxml")
    title = _meta(soup, "og:title", "twitter:title")
    if not title and soup.title and soup.title.string:
        title = soup.title.string.strip()
    description = _meta(soup, "og:description", "twitter:description", "description")
    image = _meta(soup, "og:image", "twitter:image", "twitter:image:src")
    site_name = _meta(soup, "og:site_name") or parsed.hostname

    # Resolve protocol-relative / root-relative image URLs
    if image and image.startswith("//"):
        image = f"{parsed.scheme}:{image}"
    elif image and image.startswith("/"):
        image = f"{parsed.scheme}://{parsed.hostname}{image}"

    return {
        "url": str(resp.url),
        "title": title or "",
        "description": description or "",
        "image": image or "",
        "site_name": site_name or "",
    }
