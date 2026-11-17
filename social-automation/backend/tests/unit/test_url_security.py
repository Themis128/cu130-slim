"""Coverage tests for the SSRF guard (app.core.url_security)."""

import asyncio
import ipaddress
import socket

import httpx
import pytest

from app.core.url_security import (
    SafeAsyncClient,
    URLValidationError,
    _is_blocked_ip,
    validate_url,
)


def _gai_result(ip: str, port: int = 443):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]


def _patch_dns(monkeypatch, result=None, exc=None):
    """Replace the running loop's getaddrinfo with a canned outcome."""
    loop = asyncio.get_running_loop()

    async def fake(host, port, *a, **kw):
        if exc is not None:
            raise exc
        return result

    monkeypatch.setattr(loop, "getaddrinfo", fake)


# ---------- _is_blocked_ip ----------

@pytest.mark.parametrize(
    "ip",
    ["10.0.0.1", "192.168.1.1", "127.0.0.1", "169.254.169.254",
     "224.0.0.1", "240.0.0.1", "0.0.0.0", "::1", "fd00::1", "fe80::1"],
)
def test_blocked_ips(ip):
    assert _is_blocked_ip(ipaddress.ip_address(ip)) is True


@pytest.mark.parametrize("ip", ["93.184.216.34", "8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"])
def test_public_ips_allowed(ip):
    assert _is_blocked_ip(ipaddress.ip_address(ip)) is False


# ---------- validate_url: scheme / hostname ----------

@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["ftp://example.com/x", "file:///etc/passwd", "gopher://h/1"])
async def test_bad_scheme(url):
    with pytest.raises(URLValidationError, match="scheme"):
        await validate_url(url)


@pytest.mark.asyncio
async def test_no_hostname():
    with pytest.raises(URLValidationError, match="hostname"):
        await validate_url("http:///path")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "host",
    ["localhost", "foo.localhost", "LOCALHOST.", "metadata.google.internal",
     "instance-data", "Instance-Data"],
)
async def test_blocked_hostnames(host):
    with pytest.raises(URLValidationError, match="Blocked hostname"):
        await validate_url(f"http://{host}/x")


# ---------- validate_url: IP literals ----------

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ip",
    ["127.0.0.1", "10.1.2.3", "169.254.169.254", "0.0.0.0", "224.0.0.9"],
)
async def test_blocked_ip_literals(ip):
    with pytest.raises(URLValidationError, match="Blocked IP"):
        await validate_url(f"http://{ip}/x")


@pytest.mark.asyncio
async def test_blocked_ipv6_literal():
    with pytest.raises(URLValidationError, match="Blocked IP"):
        await validate_url("http://[::1]/x")


@pytest.mark.asyncio
async def test_public_ip_literal_ok_no_dns(monkeypatch):
    called = []
    loop = asyncio.get_running_loop()

    async def fake(*a, **kw):
        called.append(1)
        return []

    monkeypatch.setattr(loop, "getaddrinfo", fake)
    await validate_url("http://93.184.216.34:8080/x")
    assert not called  # IP literals skip DNS entirely


# ---------- validate_url: DNS resolution ----------

@pytest.mark.asyncio
async def test_dns_public_ok(monkeypatch):
    _patch_dns(monkeypatch, result=_gai_result("93.184.216.34"))
    await validate_url("https://public.example/x")


@pytest.mark.asyncio
async def test_dns_httpx_url_object(monkeypatch):
    _patch_dns(monkeypatch, result=_gai_result("8.8.8.8"))
    await validate_url(httpx.URL("https://public.example/x"))


@pytest.mark.asyncio
async def test_dns_failure_raises(monkeypatch):
    _patch_dns(monkeypatch, exc=socket.gaierror("name or service not known"))
    with pytest.raises(URLValidationError, match="DNS resolution failed"):
        await validate_url("https://nope.invalid/x")


@pytest.mark.asyncio
async def test_dns_empty_result_raises(monkeypatch):
    _patch_dns(monkeypatch, result=[])
    with pytest.raises(URLValidationError, match="no addresses"):
        await validate_url("https://nope.example/x")


@pytest.mark.asyncio
async def test_dns_unparseable_address_raises(monkeypatch):
    _patch_dns(monkeypatch, result=_gai_result("not-an-ip"))
    with pytest.raises(URLValidationError, match="Unparseable"):
        await validate_url("https://weird.example/x")


@pytest.mark.asyncio
async def test_dns_skips_empty_sockaddr_then_ok(monkeypatch):
    infos = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", None)]
    infos += _gai_result("93.184.216.34")
    _patch_dns(monkeypatch, result=infos)
    await validate_url("https://public.example/x")


@pytest.mark.asyncio
@pytest.mark.parametrize("ip", ["10.0.0.9", "127.0.0.1", "169.254.169.254", "100.74.1.5"])
async def test_dns_resolves_to_blocked(monkeypatch, ip):
    _patch_dns(monkeypatch, result=_gai_result(ip))
    with pytest.raises(URLValidationError, match="resolves to blocked"):
        await validate_url("https://evil.example/x")


# ---------- SafeAsyncClient ----------

@pytest.mark.asyncio
async def test_client_blocks_private_url():
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text="ok"))
    async with SafeAsyncClient(transport=transport) as client:
        with pytest.raises(URLValidationError):
            await client.get("http://127.0.0.1/x")


@pytest.mark.asyncio
async def test_client_allow_private_bypasses_validation():
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text="ok"))
    async with SafeAsyncClient(transport=transport, allow_private_ips=True) as client:
        resp = await client.get("http://127.0.0.1/x")
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_client_merges_existing_request_hooks():
    seen = []

    async def marker(request):
        seen.append(str(request.url))

    transport = httpx.MockTransport(lambda req: httpx.Response(200))
    async with SafeAsyncClient(
        transport=transport,
        allow_private_ips=True,
        event_hooks={"request": [marker]},
    ) as client:
        await client.get("http://10.0.0.1/ping")
    assert seen == ["http://10.0.0.1/ping"]


@pytest.mark.asyncio
async def test_client_hook_enforces_public_dns(monkeypatch):
    _patch_dns(monkeypatch, result=_gai_result("93.184.216.34"))
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text="ok"))
    async with SafeAsyncClient(transport=transport) as client:
        resp = await client.get("https://public.example/x")
        assert resp.status_code == 200
