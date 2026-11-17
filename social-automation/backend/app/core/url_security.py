"""SSRF guard for outbound HTTP calls.

``SafeAsyncClient`` is a drop-in ``httpx.AsyncClient`` that validates every
outgoing request URL — including each redirect hop — before it is sent.
Requests are rejected when the target host resolves to a private, loopback,
link-local, reserved, multicast or unspecified address, or when it is a
well-known internal hostname (localhost, cloud metadata endpoints).

Set ``allow_private_ips=True`` only for intentionally internal upstreams
(e.g. in-cluster services). Validation resolves DNS at request time and
checks *all* returned addresses; it fails closed on resolution errors.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse

import httpx

_ALLOWED_SCHEMES = {"http", "https"}

# Hostnames that must never be fetched, even if DNS resolves them oddly.
_BLOCKED_HOSTNAMES = {
    "localhost",
    "metadata.google.internal",
    "instance-data",
}

# 100.64.0.0/10 (CGNAT / shared address space — Tailscale, ISP NAT) is not
# covered by ipaddress.is_private on Python < 3.12.4, so block it explicitly.
_BLOCKED_NETWORKS = (ipaddress.ip_network("100.64.0.0/10"),)


class URLValidationError(ValueError):
    """Raised when an outbound URL fails SSRF validation."""


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or any(ip in net for net in _BLOCKED_NETWORKS)
    )


async def validate_url(url: str | httpx.URL) -> None:
    """Validate that ``url`` is safe to fetch. Raises URLValidationError."""
    parsed = urlparse(str(url))
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise URLValidationError(f"Blocked URL scheme: {parsed.scheme!r}")

    host = parsed.hostname
    if not host:
        raise URLValidationError("URL has no hostname")
    host = host.rstrip(".").lower()

    if host in _BLOCKED_HOSTNAMES or host.endswith(".localhost"):
        raise URLValidationError(f"Blocked hostname: {host}")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None  # not an IP literal — resolve via DNS below

    if ip is not None:
        if _is_blocked_ip(ip):
            raise URLValidationError(f"Blocked IP address: {ip}")
        return

    try:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError) as exc:
        raise URLValidationError(f"DNS resolution failed for {host!r}: {exc}") from exc

    if not infos:
        raise URLValidationError(f"DNS resolution returned no addresses for {host!r}")

    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            raise URLValidationError(f"Unparseable address for {host!r}")
        if _is_blocked_ip(ip):
            raise URLValidationError(
                f"Host {host!r} resolves to blocked address {ip}"
            )


class SafeAsyncClient(httpx.AsyncClient):
    """httpx.AsyncClient that SSRF-validates every request, including redirects.

    ``allow_private_ips=True`` disables validation for intentionally internal
    destinations. All other constructor arguments and request methods behave
    exactly like ``httpx.AsyncClient``.
    """

    def __init__(
        self,
        *args: Any,
        allow_private_ips: bool = False,
        **kwargs: Any,
    ) -> None:
        self._allow_private_ips = allow_private_ips
        hooks = dict(kwargs.pop("event_hooks", None) or {})
        hooks["request"] = [
            *list(hooks.get("request", [])),
            self._ssrf_request_hook,
        ]
        super().__init__(*args, event_hooks=hooks, **kwargs)

    async def _ssrf_request_hook(self, request: httpx.Request) -> None:
        if self._allow_private_ips:
            return
        await validate_url(request.url)


__all__ = ["SafeAsyncClient", "URLValidationError", "validate_url"]
