"""Unit tests for the public media-URL preflight probe."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.services.media_url_probe import first_failure, probe_public_media


@pytest.mark.asyncio
async def test_probe_ok_image() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://cdn.example.com/a.jpg").mock(return_value=httpx.Response(200, headers={"content-type": "image/jpeg"}))
        out = await probe_public_media(["https://cdn.example.com/a.jpg"])
    assert out[0]["ok"] is True
    assert first_failure(out) is None


@pytest.mark.asyncio
async def test_probe_rejects_html() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://cdn.example.com/a.jpg").mock(return_value=httpx.Response(200, headers={"content-type": "text/html"}, text="<html>login</html>"))
        out = await probe_public_media(["https://cdn.example.com/a.jpg"])
    assert out[0]["ok"] is False
    assert "text/html" in out[0]["error"]
    assert first_failure(out) is out[0]


@pytest.mark.asyncio
async def test_probe_rejects_http_error() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://cdn.example.com/gone.jpg").mock(return_value=httpx.Response(404))
        out = await probe_public_media(["https://cdn.example.com/gone.jpg"])
    assert out[0]["ok"] is False
    assert out[0]["status"] == 404


@pytest.mark.asyncio
async def test_probe_tolerates_missing_content_type() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://cdn.example.com/a.bin").mock(return_value=httpx.Response(200))
        out = await probe_public_media(["https://cdn.example.com/a.bin"])
    assert out[0]["ok"] is True


@pytest.mark.asyncio
async def test_probe_network_error_is_failure_not_exception() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://cdn.example.com/x.jpg").mock(side_effect=httpx.ConnectError("down"))
        out = await probe_public_media(["https://cdn.example.com/x.jpg"])
    assert out[0]["ok"] is False
    assert "ConnectError" in out[0]["error"]


@pytest.mark.asyncio
async def test_probe_empty_list() -> None:
    assert await probe_public_media([]) == []
