"""Unit tests for SSRF URL validation used by brand extraction."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.url_safety import UnsafeUrlError, validate_public_http_url


def _fake_addrinfo(ip: str):
    """Minimal getaddrinfo result pointing at a single IP."""
    import socket

    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 6, "", (ip, 0))]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "https://127.0.0.1/admin",
        "http://localhost/",
        "https://localhost.localdomain/",
        "http://[::1]/",
        "http://0.0.0.0/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/",
        "ftp://example.com/",
        "https://user:pass@example.com/",
        "file:///etc/passwd",
    ],
)
def test_rejects_unsafe_urls(url: str):
    with patch("app.services.url_safety.socket.getaddrinfo") as mock_gai:
        # Only used when hostname is not an IP literal
        mock_gai.return_value = _fake_addrinfo("93.184.216.34")
        with pytest.raises(UnsafeUrlError):
            validate_public_http_url(url)


def test_rejects_hostname_resolving_to_private_ip():
    with patch(
        "app.services.url_safety.socket.getaddrinfo",
        return_value=_fake_addrinfo("10.1.2.3"),
    ):
        with pytest.raises(UnsafeUrlError, match="non-public"):
            validate_public_http_url("https://evil.example/brand")


def test_accepts_public_https_url():
    with patch(
        "app.services.url_safety.socket.getaddrinfo",
        return_value=_fake_addrinfo("93.184.216.34"),
    ):
        result = validate_public_http_url("cloudless.gr")
        assert result.startswith("https://cloudless.gr")


def test_accepts_public_http_url_with_path():
    with patch(
        "app.services.url_safety.socket.getaddrinfo",
        return_value=_fake_addrinfo("1.1.1.1"),
    ):
        result = validate_public_http_url("https://example.com/about?x=1")
        assert result == "https://example.com/about?x=1"


def test_strips_fragment_and_credentials_rejected():
    with patch(
        "app.services.url_safety.socket.getaddrinfo",
        return_value=_fake_addrinfo("1.1.1.1"),
    ):
        result = validate_public_http_url("https://example.com/path#section")
        assert "#" not in result
