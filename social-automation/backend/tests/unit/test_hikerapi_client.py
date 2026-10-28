"""Unit tests for app/services/hikerapi_client.py."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

import app.services.hikerapi_client as HC


def _client(key="k"):
    return HC.HikerAPIClient(api_key=key)


def _resp(status, body):
    return httpx.Response(status, json=body,
                          request=httpx.Request("GET", "https://x"))


class _AC:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None, headers=None):
        self.calls.append({"url": url, "params": params,
                           "headers": headers})
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def test_init_and_enabled(monkeypatch):
    assert _client("k").enabled is True
    monkeypatch.setattr(HC, "get_settings",
                        lambda: SimpleNamespace(HIKER_API_KEY=""))
    assert _client("").enabled is False
    assert _client("k").enabled is True


@pytest.mark.asyncio
async def test_get_disabled_raises(monkeypatch):
    monkeypatch.setattr(HC, "get_settings",
                        lambda: SimpleNamespace(HIKER_API_KEY=""))
    with pytest.raises(HC.HikerAPIError, match="not configured"):
        await _client("").get_user_by_username("x")


@pytest.mark.asyncio
async def test_get_success_and_headers():
    fake = _AC(_resp(200, {"ok": 1}))
    with patch.object(HC.httpx, "AsyncClient", return_value=fake):
        out = await _client().get_user_by_username("alice")
    assert out == {"ok": 1}
    call = fake.calls[0]
    assert call["url"].endswith("/v1/user/by/username")
    assert call["params"] == {"username": "alice"}
    assert call["headers"]["x-access-key"] == "k"


@pytest.mark.asyncio
async def test_get_http_status_error():
    fake = _AC(httpx.HTTPStatusError(
        "500", request=httpx.Request("GET", "https://x"),
        response=_resp(500, {})))
    with patch.object(HC.httpx, "AsyncClient", return_value=fake):
        with pytest.raises(HC.HikerAPIError, match="HTTP 500"):
            await _client().get_user_by_id(42)


@pytest.mark.asyncio
async def test_get_transport_error():
    fake = _AC(httpx.ConnectError("down"))
    with patch.object(HC.httpx, "AsyncClient", return_value=fake):
        with pytest.raises(HC.HikerAPIError, match="request failed"):
            await _client().get_media_info("m1")


@pytest.mark.asyncio
async def test_user_methods_params():
    fake = _AC(_resp(200, {}))
    with patch.object(HC.httpx, "AsyncClient", return_value=fake):
        c = _client()
        await c.get_user_by_id(42)
        await c.get_user_about("alice")
        await c.get_media_info("m9")
    paths = [c["url"].split("/v1/")[1] for c in fake.calls]
    assert paths == ["user/by/id", "user/about", "media/by/id"]
    assert fake.calls[0]["params"] == {"id": "42"}


@pytest.mark.asyncio
async def test_list_result_shapes():
    # list passthrough
    fake = _AC(_resp(200, [{"a": 1}]))
    with patch.object(HC.httpx, "AsyncClient", return_value=fake):
        c = _client()
        assert await c.get_user_medias("u") == [{"a": 1}]

    # dict → named key
    for body, want in [
        ({"medias": [{"m": 1}]}, [{"m": 1}]),
        ({"items": [{"i": 1}]}, [{"i": 1}]),
        ({"other": 1}, []),
    ]:
        fake = _AC(_resp(200, body))
        with patch.object(HC.httpx, "AsyncClient", return_value=fake):
            c = _client()
            assert await c.get_user_medias("u") == want

    for body, want in [
        ({"comments": [{"c": 1}]}, [{"c": 1}]),
        ([{"c": 2}], [{"c": 2}]),
        ({}, []),
    ]:
        fake = _AC(_resp(200, body))
        with patch.object(HC.httpx, "AsyncClient", return_value=fake):
            c = _client()
            assert await c.get_media_comments("m") == want

    for body, want in [
        ({"stories": [{"s": 1}]}, [{"s": 1}]),
        ([{"s": 2}], [{"s": 2}]),
        ({"items": [{"s": 3}]}, [{"s": 3}]),
    ]:
        fake = _AC(_resp(200, body))
        with patch.object(HC.httpx, "AsyncClient", return_value=fake):
            c = _client()
            assert await c.get_user_stories("u") == want
