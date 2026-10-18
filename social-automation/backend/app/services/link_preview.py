"""Fetch Open Graph / Twitter Card metadata for a URL.

Used by the Edit Post UI to show how ``link_url`` will render when the
platform builds its link card — and to populate ``link_preview_override``.

Self-hosted, no external API: a direct httpx fetch + meta-tag parse,
with a stealth-browser render fallback (cloak-fallback pattern on the
shared browser bridge) for pages behind anti-bot or client-side JS.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_MAX_BYTES = 512 * 1024  # enough for the <head> of any sane page
_TIMEOUT = 8.0
_MAX_REDIRECTS = 5
_UA = "SocialAuto-LinkPreview/1.0 (+https://cloudless.gr)"

# Direct-fetch outcomes that mean "the real page needs a browser":
# challenge/forbidden statuses and known anti-bot interstitial markers.
_BLOCKED_STATUSES = {403, 429, 503}
_CHALLENGE_MARKERS = (
    "just a moment",
    "checking your browser",
    "checking if the site connection",
    "cf-chl",
    "cf_chl",
    "datadome",
    "attention required",
    "verify you are human",
    "px-captcha",
    "perimeterx",
)
_RENDERED_HTML_CAP = 900_000  # outerHTML budget passed through the bridge
_RENDER_SETTLE_S = 2.0        # give client-rendered meta tags a beat to mount


def _resolve_public_ip(host: str) -> str:
    """Resolve ``host`` and return a public IP for the pinned request.

    Rejects when ANY resolved address is non-public (SSRF guard). ``is_global``
    (not just ``is_private``) so CGNAT/tailnet 100.64.0.0/10, benchmarking and
    other special ranges are refused too; IPv4-mapped IPv6 (``::ffff:127.0.0.1``)
    is unwrapped before checking.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ValueError("Cannot resolve host") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            raise ValueError("URL host is not publicly routable")
        return str(ip)
    raise ValueError("Cannot resolve host")


def _validate_url(url: str):
    """Parse and validate *url*, returning (ParseResult, public_ip).

    The connection target is built exclusively from validated atoms: scheme
    is checked against an allowlist and the netloc (host + port) is
    reconstructed from the resolved, publicly-routable host — so the origin
    of every fetch is pinned, never taken from the raw user string. Path,
    query and fragment are carried over from the parsed URL unchanged: they
    only select a resource on the already-validated origin and cannot
    redirect the connection elsewhere.
    """
    parsed = urlparse(url)
    scheme = parsed.scheme
    if scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("URL must be http(s) with a host")

    hostname: str = parsed.hostname  # already lower-cased by urlparse
    ip = _resolve_public_ip(hostname)

    # Rebuild a clean ParseResult from validated parts — breaks the taint
    # chain so static analysers can verify nothing from the original string
    # reaches the network call unvalidated. IPv6 literals must keep their
    # brackets or later ``parsed.port`` access on the rebuilt result raises
    # ValueError (urlparse only accepts ``host:port`` netloc for v6 when the
    # host part is bracketed).
    port_suffix = f":{parsed.port}" if parsed.port else ""
    clean_netloc = (
        f"[{hostname}]{port_suffix}" if ":" in hostname else f"{hostname}{port_suffix}"
    )
    clean = parsed._replace(scheme=scheme, netloc=clean_netloc)
    return clean, ip


def _meta(soup: BeautifulSoup, *names: str) -> str | None:
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        content = str(tag.get("content", "")).strip() if tag else ""
        if content:
            return content
    return None


def _is_challenge(body: bytes) -> bool:
    """True when the HTML is an anti-bot interstitial, not the real page."""
    head = body[:20480].decode("utf-8", errors="ignore").lower()
    return any(marker in head for marker in _CHALLENGE_MARKERS)


def _extract_preview(soup: BeautifulSoup, parsed) -> dict:
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
        "url": parsed.geturl(),
        "title": title or "",
        "description": description or "",
        "image": image or "",
        "site_name": site_name or "",
    }


async def _rendered_preview(url: str) -> dict | None:
    """Render the page in the stealth bridge browser and parse its DOM.

    cloak-fallback contract on our own infra: when a plain HTTP fetch hits
    anti-bot or a JS-shell page, the shared Chromium (patchright, already
    logged-in posture) navigates to it and returns rendered outerHTML.
    The browser lock is held only for the duration of this fetch under the
    dedicated ``web-preview`` platform tag. Best-effort — every failure
    returns None so the caller can fall back to the direct-parse result.
    """
    from app.core.config import get_settings
    from app.services.browser_bridge import BrowserBridgeClient
    from app.services.browser_orchestrator import browser_session

    bridge_url = (get_settings().BROWSER_BRIDGE_URL or "").strip()
    if not bridge_url:
        return None
    try:
        _validate_url(url)  # SSRF guard before the browser touches the URL
        bridge = BrowserBridgeClient(bridge_url, timeout=45.0, platform="web-preview")
        async with browser_session("web-preview", bridge, max_wait=25.0) as b:
            await b.navigate(url)
            await asyncio.sleep(_RENDER_SETTLE_S)
            result = await b.evaluate(
                "JSON.stringify({html: document.documentElement.outerHTML.slice(0, "
                + str(_RENDERED_HTML_CAP)
                + "), url: location.href})"
            )
        payload = result.get("result") if isinstance(result, dict) else result
        if isinstance(payload, str):
            import json as _json

            payload = _json.loads(payload)
        if not isinstance(payload, dict) or not payload.get("html"):
            return None
        # The browser may have been redirected — re-validate where it landed.
        final = str(payload.get("url") or url)
        parsed, _ = _validate_url(final)
        soup = BeautifulSoup(str(payload["html"]), "lxml")
        preview = _extract_preview(soup, parsed)
        if preview["title"] or preview["description"]:
            return preview
        return None
    except Exception as exc:  # noqa: BLE001 — fallback must never break the caller
        logger.info("link preview render fallback failed for %s: %s", url, exc)
        return None


async def fetch_link_preview(url: str) -> dict:
    """Return ``{url, title, description, image, site_name}`` for ``url``.

    Raises ``ValueError`` for invalid/non-public URLs and ``httpx.HTTPError``
    for fetch failures.
    """
    current = url.strip()
    parsed, ip = _validate_url(current)

    # Redirects are followed manually so every hop's host is re-validated —
    # with follow_redirects=True httpx would happily follow a public URL's
    # 302 to http://169.254.169.254/ or a LAN/tailnet service.
    async with httpx.AsyncClient(
        timeout=_TIMEOUT,
        follow_redirects=False,
        headers={"User-Agent": _UA, "Accept": "text/html,*/*;q=0.8"},
    ) as client:
        for _hop in range(_MAX_REDIRECTS + 1):
            # Connect to the IP we just validated, not the hostname — closes
            # the DNS-rebinding TOCTOU where the host resolves public during
            # validation but private at connect time. The original Host header
            # (and SNI for TLS) still reaches the right vhost/certificate.
            ip_host = f"[{ip}]" if ":" in ip else ip
            netloc = ip_host + (f":{parsed.port}" if parsed.port else "")
            pinned = parsed._replace(netloc=netloc).geturl()
            host_header = (parsed.hostname or "") + (f":{parsed.port}" if parsed.port else "")
            ext = (
                {"sni_hostname": parsed.hostname}
                if parsed.scheme == "https"
                else {}
            )
            async with client.stream(
                "GET", pinned, headers={"Host": host_header}, extensions=ext
            ) as resp:
                if resp.is_redirect:
                    location = resp.headers.get("location")
                    if not location:
                        raise ValueError("Redirect without Location header")
                    current = urljoin(str(resp.url), location)
                    parsed, ip = _validate_url(current)
                    continue
                if resp.status_code in _BLOCKED_STATUSES:
                    # Bot gate (Cloudflare/Datadome) — try the stealth render
                    # path before surfacing the fetch error.
                    rendered = await _rendered_preview(url)
                    if rendered is not None:
                        return rendered
                resp.raise_for_status()
                # Stream with a hard cap instead of buffering the whole body.
                chunks: list[bytes] = []
                size = 0
                async for chunk in resp.aiter_bytes():
                    chunks.append(chunk)
                    size += len(chunk)
                    if size >= _MAX_BYTES:
                        break
                body = b"".join(chunks)[:_MAX_BYTES]
                final_url = parsed.geturl()  # report the hostname form, not the pinned IP
                break
        else:
            raise ValueError("Too many redirects")

    if _is_challenge(body):
        rendered = await _rendered_preview(url)
        if rendered is not None:
            return rendered

    preview = _extract_preview(BeautifulSoup(body, "lxml"), parsed)
    preview["url"] = final_url
    if not preview["title"] and not preview["description"]:
        # Empty parse on a JS-shell page — one render attempt before
        # returning the empty preview.
        rendered = await _rendered_preview(url)
        if rendered is not None:
            return rendered
    return preview
