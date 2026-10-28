"""URL safety helpers to prevent SSRF when fetching user-supplied URLs."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse, urlunparse

# Hostnames that must never be fetched, even if DNS resolves oddly.
_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata.google.internal",
        "metadata",
        "kubernetes.default",
        "kubernetes.default.svc",
    }
)


class UnsafeUrlError(ValueError):
    """Raised when a URL is rejected as unsafe for outbound fetch."""


def _is_blocked_hostname(hostname: str) -> bool:
    host = hostname.lower().rstrip(".")
    if host in _BLOCKED_HOSTNAMES:
        return True
    if host.endswith(".localhost") or host.endswith(".local") or host.endswith(".internal"):
        return True
    return False


def _ip_is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True only for globally routable addresses."""
    # is_global excludes private, loopback, link-local, multicast, reserved, etc.
    return bool(ip.is_global)


def _hostname_resolves_public(hostname: str) -> bool:
    """Resolve hostname and require every A/AAAA record to be public."""
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None

    if literal is not None:
        return _ip_is_public(literal)

    try:
        results = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"Could not resolve host: {hostname}") from exc

    if not results:
        raise UnsafeUrlError(f"Could not resolve host: {hostname}")

    for _family, _type, _proto, _canon, sockaddr in results:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        if not _ip_is_public(ip):
            return False
    return True


def validate_public_http_url(url: str, *, allow_http: bool = True) -> str:
    """Validate and normalize a user-supplied URL for safe server-side fetch.

    Rejects non-http(s) schemes, missing hosts, blocked hostnames, IP literals
    that are not globally routable, and hostnames that resolve to non-public IPs.

    Returns a normalized URL string (scheme/host/path/query/fragment rebuilt
    from parsed components) safe to pass to httpx.
    """
    raw = (url or "").strip()
    if not raw:
        raise UnsafeUrlError("URL is required")

    # Reject explicit non-http schemes before any normalization.
    lower = raw.lower()
    if "://" in lower:
        scheme = lower.split("://", 1)[0]
        if scheme not in ("http", "https"):
            raise UnsafeUrlError("Only http and https URLs are allowed")
    elif not raw.startswith(("http://", "https://")):
        raw = "https://" + raw

    parsed = urlparse(raw)
    allowed_schemes = ("http", "https") if allow_http else ("https",)
    if parsed.scheme not in allowed_schemes:
        raise UnsafeUrlError("Only http and https URLs are allowed")

    hostname = parsed.hostname
    if not hostname:
        raise UnsafeUrlError("URL must include a hostname")

    if _is_blocked_hostname(hostname):
        raise UnsafeUrlError("Hostname is not allowed")

    # Reject URLs with embedded credentials (userinfo) — often used in SSRF tricks.
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeUrlError("URLs with credentials are not allowed")

    if not _hostname_resolves_public(hostname):
        raise UnsafeUrlError("URL resolves to a non-public address")

    # Rebuild from validated components (drops unexpected netloc tricks).
    netloc = hostname
    if parsed.port:
        netloc = f"{hostname}:{parsed.port}"

    return urlunparse(
        (parsed.scheme, netloc, parsed.path or "/", parsed.params, parsed.query, "")
    )
