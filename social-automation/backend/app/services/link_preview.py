"""Fetch Open Graph / Twitter Card metadata for a URL.

Used by the Edit Post UI to show how ``link_url`` will render when the
platform builds its link card — and to populate ``link_preview_override``.

Self-hosted, no external API: a direct httpx fetch + meta-tag parse.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

_MAX_BYTES = 512 * 1024  # enough for the <head> of any sane page
_TIMEOUT = 8.0
_MAX_REDIRECTS = 5
_UA = "SocialAuto-LinkPreview/1.0 (+https://cloudless.gr)"


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

    The returned ``ParseResult`` is reconstructed from individually validated
    components (scheme, host, port, path, query, fragment) so the taint chain
    from the raw user string is broken — the fetch target is built exclusively
    from validated atoms, not from the original URL bytes.
    """
    parsed = urlparse(url)
    scheme = parsed.scheme
    if scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("URL must be http(s) with a host")

    hostname: str = parsed.hostname  # already lower-cased by urlparse
    ip = _resolve_public_ip(hostname)

    # Rebuild a clean ParseResult from validated parts — breaks the taint
    # chain so static analysers can verify nothing from the original string
    # reaches the network call unvalidated.
    port_suffix = f":{parsed.port}" if parsed.port else ""
    clean_netloc = f"{hostname}{port_suffix}"
    clean = parsed._replace(scheme=scheme, netloc=clean_netloc)
    return clean, ip


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
        "url": final_url,
        "title": title or "",
        "description": description or "",
        "image": image or "",
        "site_name": site_name or "",
    }
